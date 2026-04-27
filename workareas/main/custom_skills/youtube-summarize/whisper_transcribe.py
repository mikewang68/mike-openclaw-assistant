#!/usr/bin/env python3
"""
分段 Whisper 转写脚本（按9MB大小切分）
用法: python3 whisper_transcribe.py <音频wav路径> <输出txt路径>
"""
import requests, time, os, subprocess, re, json, sys, wave, struct

INPUT_WAV = sys.argv[1] if len(sys.argv) > 1 else "/obsidian/audio/{video_id}.wav"
OUTPUT_TXT = sys.argv[2] if len(sys.argv) > 2 else "/obsidian/temp_chunks/{video_id}_transcript.txt"
WHISPER_API = "http://192.168.1.2:9000/v1/audio/transcriptions"
CHUNK_SIZE_MB = 9
TEMP_DIR = "/obsidian/temp_chunks"

os.makedirs(TEMP_DIR, exist_ok=True)

# 读取进度
video_id = os.path.splitext(os.path.basename(INPUT_WAV))[0]
PROGRESS_FILE = f"{TEMP_DIR}/{video_id}_progress.json"

done_chunks = set()
if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE) as f:
        done_chunks = set(json.load(f).get('done', []))

# 按9MB大小切分音频
def split_audio_by_size(wav_path, chunk_dir, max_size_mb=9):
    """严格按9MB文件大小切割WAV音频"""
    import wave as _wave
    import struct as _struct

    os.makedirs(chunk_dir, exist_ok=True)
    max_size_bytes = max_size_mb * 1024 * 1024

    with _wave.open(wav_path, 'rb') as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        byte_rate = framerate * nchannels * sampwidth
        pcm_data = wf.readframes(nframes)

    total_pcm_size = len(pcm_data)
    total_duration = total_pcm_size / byte_rate

    def make_wav_header(data_size):
        chunk_size = 36 + data_size
        return (
            b'RIFF' + _struct.pack('<I', chunk_size) + b'WAVE' +
            b'fmt ' + _struct.pack('<I', 16) +
            _struct.pack('<H', 1) +
            _struct.pack('<H', nchannels) +
            _struct.pack('<I', framerate) +
            _struct.pack('<I', byte_rate) +
            _struct.pack('<H', nchannels * sampwidth) +
            _struct.pack('<H', sampwidth * 8) +
            b'data' + _struct.pack('<I', data_size)
        )

    chunks = []
    offset = 0
    chunk_idx = 0

    while offset < total_pcm_size:
        chunk_pcm_size = min(max_size_bytes, total_pcm_size - offset)
        chunk_path = f"{chunk_dir}/chunk_{chunk_idx:04d}.wav"

        with open(chunk_path, 'wb') as f:
            f.write(make_wav_header(chunk_pcm_size))
            f.write(pcm_data[offset:offset + chunk_pcm_size])

        start_sec = offset / byte_rate
        end_sec = (offset + chunk_pcm_size) / byte_rate
        chunks.append({
            'index': chunk_idx,
            'start': start_sec,
            'end': end_sec,
            'path': chunk_path
        })

        offset += chunk_pcm_size
        chunk_idx += 1

    return chunks

# 读取已完成的transcript
all_lines = []
if os.path.exists(OUTPUT_TXT):
    with open(OUTPUT_TXT) as f:
        all_lines = [L.strip() for L in f if L.strip()]

print(f"开始按9MB切分: {INPUT_WAV}")
chunk_dir = f"{TEMP_DIR}/{video_id}_chunks"
chunks = split_audio_by_size(INPUT_WAV, chunk_dir, CHUNK_SIZE_MB)
print(f"分块完成：共 {len(chunks)} 块")

for chunk in chunks:
    idx = chunk['index']
    path = chunk['path']
    start = int(chunk['start'])

    if idx in done_chunks:
        print(f"[{idx:02d}] 已完成，跳过")
        continue

    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"[{idx:02d}] {chunk['start']:.1f}s-{chunk['end']:.1f}s ({size_mb:.1f}MB)...", end='', flush=True)

    if os.path.getsize(path) < 1000:
        print(" 跳过(无效)")
        done_chunks.add(idx)
        continue

    try:
        with open(path, 'rb') as f:
            files = {'file': ('chunk.wav', f, 'audio/wav')}
            data = {'model': 'whisper-1', 'language': 'zh', 'response_format': 'text'}
            resp = requests.post(WHISPER_API, files=files, data=data, timeout=300)

        if resp.status_code == 200:
            text = resp.text.strip()
            if text:
                line = f"[{idx:02d}] {text}"
                all_lines.append(line)
                with open(OUTPUT_TXT, 'w') as out:
                    out.write('\n'.join(all_lines))
                print(f" ✅ {len(text)}字 (累计{len(all_lines)}段)")
            else:
                print(f" ✅ (空)")
            done_chunks.add(idx)
            with open(PROGRESS_FILE, 'w') as f:
                json.dump({'done': list(done_chunks)}, f)
        else:
            print(f" ❌ {resp.status_code}")
    except Exception as e:
        print(f" ❌ {e}")
    finally:
        if os.path.exists(path):
            os.remove(path)

# 清理进度文件
if os.path.exists(PROGRESS_FILE):
    os.remove(PROGRESS_FILE)

print(f"\n完成！共{len(all_lines)}段，已保存: {OUTPUT_TXT}")