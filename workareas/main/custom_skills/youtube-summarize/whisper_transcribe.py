#!/usr/bin/env python3
"""
分段 Whisper 转写脚本
用法: python3 whisper_transcribe.py <音频wav路径> <输出txt路径>
"""
import requests, time, os, subprocess, re, json, sys

INPUT_WAV = sys.argv[1] if len(sys.argv) > 1 else "/obsidian/audio/{video_id}.wav"
OUTPUT_TXT = sys.argv[2] if len(sys.argv) > 2 else "/obsidian/temp_chunks/{video_id}_transcript.txt"
WHISPER_API = "http://192.168.1.2:9000/v1/audio/transcriptions"
CHUNK_DURATION = 60  # 每段秒数
TEMP_DIR = "/obsidian/temp_chunks"

os.makedirs(TEMP_DIR, exist_ok=True)

# 读取进度
video_id = os.path.splitext(os.path.basename(INPUT_WAV))[0]
PROGRESS_FILE = f"{TEMP_DIR}/{video_id}_progress.json"

done_chunks = set()
if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE) as f:
        done_chunks = set(json.load(f).get('done', []))

# 获取音频时长
result = subprocess.run(
    ['/program/tools/ffmpeg', '-i', INPUT_WAV],
    capture_output=True, text=True
)
m = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})', result.stderr)
if not m:
    print("无法获取音频时长")
    sys.exit(1)
total_secs = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))
chunks_total = (total_secs + CHUNK_DURATION - 1) // CHUNK_DURATION
print(f"音频总时长: {total_secs}秒 ({chunks_total}段)")

# 读取已完成的transcript
all_lines = []
if os.path.exists(OUTPUT_TXT):
    with open(OUTPUT_TXT) as f:
        all_lines = [L.strip() for L in f if L.strip()]

for start in range(0, total_secs, CHUNK_DURATION):
    chunk_idx = start // CHUNK_DURATION
    if start in done_chunks:
        print(f"[{start//60:02d}:00] 已完成，跳过")
        continue

    chunk_file = f"{TEMP_DIR}/chunk_{start:06d}.wav"
    end = min(start + CHUNK_DURATION, total_secs)
    print(f"[{start//60:02d}:00] 截取 {start}s-{end}s...", end='', flush=True)

    subprocess.run([
        '/program/tools/ffmpeg', '-i', INPUT_WAV,
        '-ss', str(start), '-t', str(CHUNK_DURATION),
        '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
        chunk_file, '-y'
    ], capture_output=True)

    if not os.path.exists(chunk_file) or os.path.getsize(chunk_file) < 1000:
        print(" 跳过(无效)")
        done_chunks.add(start)
        continue

    try:
        with open(chunk_file, 'rb') as f:
            files = {'file': ('chunk.wav', f, 'audio/wav')}
            data = {'model': 'whisper-1', 'language': 'zh', 'response_format': 'text'}
            resp = requests.post(WHISPER_API, files=files, data=data, timeout=120)
        if resp.status_code == 200:
            text = resp.text.strip()
            if text:
                line = f"[{start//60:02d}:00] {text}"
                all_lines.append(line)
                with open(OUTPUT_TXT, 'w') as out:
                    out.write('\n'.join(all_lines))
                print(f" ✅ {len(text)}字 (累计{len(all_lines)}段)")
            else:
                print(f" ✅ (空)")
            done_chunks.add(start)
            with open(PROGRESS_FILE, 'w') as f:
                json.dump({'done': list(done_chunks)}, f)
        else:
            print(f" ❌ {resp.status_code}")
    except Exception as e:
        print(f" ❌ {e}")
    finally:
        # 清理 chunk 文件
        if os.path.exists(chunk_file):
            os.remove(chunk_file)

# 清理进度文件
if os.path.exists(PROGRESS_FILE):
    os.remove(PROGRESS_FILE)

print(f"\n完成！共{len(all_lines)}段，已保存: {OUTPUT_TXT}")
