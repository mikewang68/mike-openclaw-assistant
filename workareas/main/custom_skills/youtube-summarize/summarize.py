#!/usr/bin/env python3
"""
youtube-summarize — YouTube 视频总结 Skill

工作流程：
  模式一（字幕）：YouTube字幕 → 直接LLM总结
  模式二（无字幕）：下载音频(→/obsidian/audio) → 按9MB分块(→/obsidian/temp_chunks)
                → 逐块Whisper转写 → 合并文本 → LLM总结 → 保存文件 → 清理临时文件

用法：
  python3 summarize.py <YouTube_URL> [--output DIR]
"""

import shutil
import sys
import re
import json
import os
import glob
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

# ─── 常量 ─────────────────────────────────────────────
FFMPEG         = "/program/tools/ffmpeg"
WHISPER_API    = "http://192.168.1.2:9000/v1/audio/transcriptions"
WHISPER_MODEL  = "Systran/faster-whisper-tiny"
WHISPER_TIMEOUT = 300
CHUNK_SIZE_MB  = 9
AUDIO_DIR      = "/obsidian/audio"
TEMP_DIR       = "/obsidian/temp_chunks"
DEFAULT_OUTPUT = "/obsidian/01_Input/01_视频"
LLM_API_KEY_FILE = "/home/node/.openclaw/agents/main/agent/auth-profiles.json"

# ─── 懒加载工具函数（先用，没有再装） ──────────────────────────

def _get_pip():
    """返回 pip 路径，懒加载：先用，没有再装"""
    pip_paths = [
        "/home/node/.local/bin/pip",
        "/home/node/.local/bin/pip3",
        "/home/node/.local/bin/pip3.11",
    ]
    for p in pip_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return [p, "--break-system-packages"]
    # 没有 pip，先装 pip
    print("[INFO] pip 未找到，正在安装...")
    subprocess.run([sys.executable, "/tmp/get-pip.py", "--break-system-packages", "-q"],
                   capture_output=True)
    return ["/home/node/.local/bin/pip", "--break-system-packages"]


def _ensure_yt_dlp():
    """确保 yt-dlp 可用，没有则安装，找不到则报错（让用户知道）"""
    yt_dlp_path = "/home/node/yt-dlp"
    if os.path.isfile(yt_dlp_path) and os.access(yt_dlp_path, os.X_OK):
        return yt_dlp_path
    # 尝试在 PATH 中找
    for candidate in ["/home/node/.local/bin/yt-dlp", "/usr/local/bin/yt-dlp"]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            # 软链接到标准位置
            os.makedirs(os.path.dirname(yt_dlp_path), exist_ok=True)
            if os.path.islink(yt_dlp_path):
                os.unlink(yt_dlp_path)
            os.symlink(candidate, yt_dlp_path)
            return yt_dlp_path
    # 真的没有，安装
    print("[INFO] yt-dlp 未找到，正在安装...")
    bin_dir = "/home/node/.local/bin"
    os.makedirs(bin_dir, exist_ok=True)
    dl_path = os.path.join(bin_dir, "yt-dlp")
    subprocess.run([
        "curl", "-sL",
        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
        "-o", dl_path
    ], timeout=30)
    os.chmod(dl_path, 0o755)
    os.makedirs(os.path.dirname(yt_dlp_path), exist_ok=True)
    if os.path.islink(yt_dlp_path):
        os.unlink(yt_dlp_path)
    os.symlink(dl_path, yt_dlp_path)
    return yt_dlp_path


def _ensure_youtube_transcript_api():
    """确保 youtube-transcript-api 可用，没有则安装"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        return YouTubeTranscriptApi
    except ImportError:
        pass
    # 没有，装
    print("[INFO] youtube-transcript-api 未安装，正在安装...")
    pip_cmd, pip_args = _get_pip()
    subprocess.run([pip_cmd] + pip_args + ["install", "youtube-transcript-api", "-q"],
                   capture_output=True)
    from youtube_transcript_api import YouTubeTranscriptApi
    return YouTubeTranscriptApi


# ─── 工具函数 ─────────────────────────────────────────

def extract_video_id(url: str) -> str:
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'^([0-9A-Za-z_-]{11})$',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"无法从 URL 提取视频ID: {url}")


def get_llm_api_key() -> str:
    try:
        with open(LLM_API_KEY_FILE) as f:
            data = json.load(f)
        for p in data.get('profiles', {}).values():
            if p.get('provider') == 'minimax-cn':
                return p.get('key', '')
    except Exception:
        pass
    return ''


def call_llm(prompt: str, model: str = "MiniMax-M2.7", temperature: float = 0.3) -> str:
    api_key = get_llm_api_key()
    if not api_key:
        print("[WARN] 未找到 LLM API Key，跳过 LLM 总结", file=sys.stderr)
        return ''

    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 4096,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers=headers, method='POST'
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            return result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
    except Exception as e:
        print(f"[WARN] LLM 调用失败: {e}", file=sys.stderr)
        return ''


# ─── 视频元数据 ───────────────────────────────────────

def get_video_metadata(url: str) -> dict:
    yt_dlp = _ensure_yt_dlp()
    try:
        result = subprocess.run(
            [yt_dlp, '--dump-json', '--no-download', url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                'title':       data.get('title', ''),
                'description': data.get('description', ''),
                'duration':    data.get('duration', 0),
                'tags':        data.get('tags', []),
                'channel':     data.get('channel', ''),
                'view_count':  data.get('view_count', 0),
                'upload_date': data.get('upload_date', ''),
            }
    except Exception as e:
        print(f"[WARN] yt-dlp 元数据获取失败: {e}", file=sys.stderr)
    return {'title': '', 'description': '', 'duration': 0,
            'tags': [], 'channel': '', 'view_count': 0, 'upload_date': ''}


# ─── 字幕获取（模式一） ─────────────────────────────────

def get_transcript(video_id: str):
    YouTubeTranscriptApi = _ensure_youtube_transcript_api()
    api = YouTubeTranscriptApi()
    transcript_list = api.list(video_id)

    lang_order = ['zh', 'zh-Hans', 'zh-TW', 'zh-Hant', 'en', 'en-US']
    target = None
    for lang in lang_order:
        for t in transcript_list:
            if t.language_code == lang or lang in t.language_code:
                target = t
                break
        if target:
            break

    if not target:
        for t in transcript_list:
            target = t
            break

    if not target:
        raise ValueError("无可用字幕")

    fetched = target.fetch()
    segments = []
    for s in fetched.snippets:
        segments.append({
            'start':    float(s.start),
            'duration': float(s.duration),
            'text':     s.text,
            'timestamp': f"{int(s.start//3600):02d}:{int(s.start%3600//60):02d}:{int(s.start%60):02d}",
        })
    return target.language_code, segments


# ─── 音频下载 ─────────────────────────────────────────

def download_audio(url: str, output_path: str) -> str:
    """下载音视频并转为 16kHz mono WAV，保存到 output_path"""
    yt_dlp = _ensure_yt_dlp()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_audio = output_path.replace('.wav', '.tmp')

    result = subprocess.run(
        [yt_dlp, '-f', 'bestaudio/best',
         '-o', tmp_audio + '.%(ext)s',
         '--no-playlist', '--no-post-overwrites',
         url],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr}")

    # 找到下载的音频文件
    found = None
    for ext in ['.webm', '.mp3', '.m4a', '.ogg', '.aac', '.wav']:
        candidate = tmp_audio + ext
        if os.path.exists(candidate):
            found = candidate
            break
    if not found:
        raise RuntimeError("yt-dlp 未生成音频文件")

    # ffmpeg 转换为 16kHz WAV
    conv_result = subprocess.run(
        [FFMPEG, '-i', found,
         '-vn', '-acodec', 'pcm_s16le',
         '-ar', '16000', '-ac', '1',
         '-y', output_path],
        capture_output=True, text=True, timeout=120
    )
    if found != output_path and os.path.exists(found):
        os.remove(found)
    if not os.path.exists(output_path):
        raise RuntimeError(f"ffmpeg 转换失败: {conv_result.stderr}")
    return output_path


# ─── 按大小分块 ───────────────────────────────────────

def split_audio_by_size(wav_path: str, chunk_dir: str, max_size_mb: int = CHUNK_SIZE_MB) -> list:
    """严格按9MB文件大小切割WAV音频，返回每块信息列表。

    策略：读取WAV的PCM原始数据，按9MB等大小切分，每段追加标准44字节WAV头。
    这样每块严格<=9MB，无时间估算误差。"""
    import wave as _wave
    import struct as _struct
    os.makedirs(chunk_dir, exist_ok=True)
    max_size_bytes = max_size_mb * 1024 * 1024  # 9MB

    # ── 读取WAV信息 ──
    with _wave.open(wav_path, 'rb') as wf:
        nchannels = wf.getnchannels()     # 1
        sampwidth = wf.getsampwidth()     # 2 bytes
        framerate = wf.getframerate()     # 16000 Hz
        nframes = wf.getnframes()         # 总帧数
        byte_rate = framerate * nchannels * sampwidth
        pcm_data = wf.readframes(nframes)  # 读取全部PCM数据

    total_pcm_size = len(pcm_data)  # 字节数
    total_duration = total_pcm_size / byte_rate
    print(f"  WAV: {nchannels}ch {sampwidth*8}bit {framerate}Hz, PCM={total_pcm_size/1024/1024:.1f}MB, 时长={total_duration:.1f}s")

    # 构造标准44字节WAV头（固定参数：16bit mono 16000Hz）
    def make_wav_header(data_size: int):
        chunk_size = 36 + data_size
        return (
            b'RIFF' +
            _struct.pack('<I', chunk_size) +
            b'WAVE' +
            b'fmt ' +
            _struct.pack('<I', 16) +          # Subchunk1Size = 16 (PCM)
            _struct.pack('<H', 1) +            # AudioFormat = 1 (PCM)
            _struct.pack('<H', nchannels) +   # NumChannels
            _struct.pack('<I', framerate) +  # SampleRate
            _struct.pack('<I', byte_rate) +  # ByteRate
            _struct.pack('<H', nchannels * sampwidth) +  # BlockAlign
            _struct.pack('<H', sampwidth * 8) +  # BitsPerSample
            b'data' +
            _struct.pack('<I', data_size)     # Subchunk2Size
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

        actual_mb = os.path.getsize(chunk_path) / 1024 / 1024
        start_sec = offset / byte_rate
        end_sec = (offset + chunk_pcm_size) / byte_rate
        is_last = (offset + chunk_pcm_size >= total_pcm_size - 1)
        tag = " [最后一块]" if is_last else ""
        print(f"  [{chunk_idx:02d}] {start_sec:.1f}s-{end_sec:.1f}s ({chunk_pcm_size/1024/1024:.1f}MB → {actual_mb:.1f}MB){tag}")

        chunks.append({
            'index': chunk_idx,
            'start': start_sec,
            'end': end_sec,
            'path': chunk_path,
            'size_mb': actual_mb
        })
        offset += chunk_pcm_size
        chunk_idx += 1

    print(f"  分块完成：共 {len(chunks)} 块，总计 {sum(c['size_mb'] for c in chunks):.1f}MB")
    return chunks


# ─── Whisper 转写 ─────────────────────────────────────

def transcribe_chunk(chunk_path: str) -> str:
    """转写单个音频块，返回文本"""
    import requests
    with open(chunk_path, 'rb') as f:
        files = {'file': (os.path.basename(chunk_path), f, 'audio/wav')}
        data = {
            'model':          WHISPER_MODEL,
            'language':       'zh',
            'response_format': 'text',
        }
        resp = requests.post(
            WHISPER_API, files=files, data=data,
            timeout=WHISPER_TIMEOUT
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Whisper {resp.status_code}: {resp.text[:200]}")
    return resp.text.strip()


def transcribe_all_chunks(chunks: list, video_id: str) -> str:
    """逐块转写，合并为完整文本"""
    all_texts = []
    for chunk in chunks:
        idx = chunk['index']
        path = chunk['path']
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"  转写块 {idx:02d}/{len(chunks)} ({size_mb:.1f}MB)...", end='', flush=True)
        try:
            text = transcribe_chunk(path)
            if text:
                print(f" ✅ {len(text)}字")
                all_texts.append(f"[{idx:02d}] {text}")
            else:
                print(f" ✅ (空)")
                all_texts.append(f"[{idx:02d}] ")
        except Exception as e:
            print(f" ❌ {e}")
            all_texts.append(f"[{idx:02d}] [转写失败]")
    return '\n'.join(all_texts)


# ─── 清理临时文件 ─────────────────────────────────────

def cleanup_audio_and_chunks(audio_path: str, chunk_dir: str, video_id: str):
    """删除本次任务创建的文件（audio/{video_id}.wav + chunk_dir/*）
    ⚠️ 只删除本次视频相关的文件，不影响其他进程创建的文件"""
    if audio_path and os.path.exists(audio_path):
        try:
            os.remove(audio_path)
            print(f"[CLEAN] 删除音频: {audio_path}")
        except Exception as e:
            print(f"[WARN] 删除音频失败: {e}")

    if os.path.isdir(chunk_dir):
        for f in os.listdir(chunk_dir):
            fp = os.path.join(chunk_dir, f)
            try:
                os.remove(fp)
            except Exception:
                pass
        try:
            os.rmdir(chunk_dir)
            print(f"[CLEAN] 删除目录: {chunk_dir}")
        except Exception:
            pass


# ─── LLM 总结 ────────────────────────────────────────

def build_summary_prompt(title: str, channel: str, duration: int,
                         transcript: str, url: str) -> str:
    minutes = duration // 60
    date_str = datetime.now().strftime('%Y-%m-%d')

    return f"""你是一个专业的技术视频内容总结专家。请根据以下视频字幕，生成结构化Markdown总结。

## 视频信息
- 标题：{title}
- 频道：{channel}
- 时长：{minutes}分钟
- 链接：{url}
- 总结日期：{date_str}

## 字幕/转写内容
{transcript[:15000]}

## 输出要求
请按以下结构生成总结，用中文输出 Markdown 格式：

### 基本信息
视频概要（一句话总结，1-2句）

### 内容摘要
按时间线或主题分段总结，每段包含关键观点、重要数据和出处

### 核心要点：XX 技术演进路线（如适用）
用表格或时间线形式梳理技术版本演进

### 关键技术解析
挑选视频中重点讲解的3-5个技术点，用"是什么 + 为什么重要 + 核心原理"结构说明

### 个人点评
对视频内容的评价：价值、局限性、启发

注意：
1. 如果转写质量差（大量无意义文本），请标注"转写质量较差，内容可能不准确"
2. 技术细节需准确，无法确认的部分请注明"（待核实）"
3. 提取视频中的具体信息（人名、机构名、数据）
"""


def generate_summary(metadata: dict, transcript: str, url: str) -> str:
    prompt = build_summary_prompt(
        title    = metadata.get('title', ''),
        channel  = metadata.get('channel', ''),
        duration = metadata.get('duration', 0),
        transcript= transcript,
        url      = url,
    )
    return call_llm(prompt)


# ─── 主流程 ──────────────────────────────────────────

def main():
    url = None
    output_dir = DEFAULT_OUTPUT

    for i, arg in enumerate(sys.argv[1:]):
        if arg.startswith('http'):
            url = arg
        elif arg in ('--output', '-o') and i + 2 < len(sys.argv):
            output_dir = sys.argv[sys.argv.index(arg) + 1]

    if not url:
        print("用法: python3 summarize.py <YouTube_URL> [--output DIR]", file=sys.stderr)
        sys.exit(1)

    video_id = extract_video_id(url)
    print(f"[INFO] 视频ID: {video_id}")

    # ── 步骤1：获取元数据 ──
    print("[INFO] 获取视频元数据...")
    metadata = get_video_metadata(url)

    mode = "unknown"
    lang = "unknown"
    full_text = ""
    transcript_file = None

    # ── 步骤2：字幕优先 ──
    try:
        lang, segments = get_transcript(video_id)
        full_text = ' '.join([s['text'] for s in segments])
        mode = "transcript"
        print(f"[INFO] 字幕获取成功，语言: {lang}，字数: {len(full_text)}")
    except Exception as e:
        print(f"[WARN] 字幕获取失败: {e}，切换 Whisper 模式", file=sys.stderr)

        # ── 步骤2b：下载音频 → /obsidian/audio ──
        audio_path = f"{AUDIO_DIR}/{video_id}.wav"
        chunk_dir  = f"{TEMP_DIR}/{video_id}_chunks"

        try:
            print(f"[INFO] 下载音频 → {audio_path}...")
            download_audio(url, audio_path)
            audio_size_mb = os.path.getsize(audio_path) / 1024 / 1024
            print(f"[INFO] 音频大小: {audio_size_mb:.1f}MB")
        except Exception as e:
            print(f"[ERROR] 音频下载失败: {e}")
            sys.exit(1)

        # ── 步骤3：分块 → /obsidian/temp_chunks ──
        print(f"[INFO] 按 {CHUNK_SIZE_MB}MB 分块...")
        chunks = split_audio_by_size(audio_path, chunk_dir, CHUNK_SIZE_MB)
        if not chunks:
            print("[ERROR] 分块失败，无有效块")
            sys.exit(1)

        # ── 步骤4：逐块 Whisper 转写 ──
        print(f"[INFO] 开始转写 {len(chunks)} 个音频块...")
        full_text = transcribe_all_chunks(chunks, video_id)

        transcript_file = f"{TEMP_DIR}/{video_id}_transcript.txt"
        with open(transcript_file, 'w', encoding='utf-8') as f:
            f.write(full_text)
        print(f"[INFO] 转写完成，累计 {len(full_text)} 字")

        mode = "whisper"
        lang = "zh"

        # ── 步骤5：清理临时文件 ──
        print("[INFO] 清理临时文件...")
        cleanup_audio_and_chunks(audio_path, chunk_dir, video_id)
        if transcript_file and os.path.exists(transcript_file):
            os.remove(transcript_file)
            print(f"[CLEAN] 删除 transcript: {transcript_file}")

    # ── 步骤6：LLM 总结 ──
    print("[INFO] 调用 LLM 生成总结...")
    summary = generate_summary(metadata, full_text, url)

    if not summary:
        print("[WARN] LLM 总结失败")
        summary = f"# {metadata.get('title', '视频总结')}\n\n**LLM 总结生成失败。**\n\n原始文本：\n\n{full_text[:3000]}"

    # ── 步骤7：保存文件 ──
    date_str = datetime.now().strftime('%Y-%m-%d')
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', metadata.get('title', 'untitled'))[:50]
    filename = f"{date_str}-youtube-{safe_title}.md"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f"[INFO] 已保存: {filepath}")

    # ── 输出 JSON ──
    result = {
        "video_id":    video_id,
        "url":         url,
        "mode":        mode,
        "language":    lang,
        "title":       metadata.get('title', ''),
        "channel":     metadata.get('channel', ''),
        "duration":    metadata.get('duration', 0),
        "transcript_chars": len(full_text),
        "summary_file": filepath,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
