#!/usr/bin/env python3
"""
youtube-summarize — YouTube 视频总结 Skill

工作流程：
  模式一（字幕）：YouTube字幕 → 直接LLM总结
  模式二（无字幕）：下载音频(→/obsidian/audio) → 按10MB分块(→/obsidian/temp_chunks)
                → 逐块Whisper转写 → 合并文本 → LLM总结 → 保存文件 → 清理临时文件

用法：
  python3 summarize.py <YouTube_URL> [--output DIR]
"""

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
YT_DLP         = "/home/node/.local/bin/yt-dlp"
WHISPER_API    = "http://192.168.1.2:9000/v1/audio/transcriptions"
WHISPER_MODEL  = "Systran/faster-whisper-tiny"   # 可选：tiny/base/small/large-v3
WHISPER_TIMEOUT = 300                            # 单块超时（秒）
CHUNK_SIZE_MB  = 10                              # 音频分块大小（MB）
AUDIO_DIR      = "/obsidian/audio"
TEMP_DIR       = "/obsidian/temp_chunks"
DEFAULT_OUTPUT = "/obsidian/01_Input/01_视频"
LLM_API_KEY_FILE = "/home/node/.openclaw/agents/main/agent/auth-profiles.json"


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
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
            return result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
    except Exception as e:
        print(f"[WARN] LLM 调用失败: {e}", file=sys.stderr)
        return ''


# ─── 视频元数据 ───────────────────────────────────────

def get_video_metadata(url: str) -> dict:
    try:
        result = subprocess.run(
            [YT_DLP, '--dump-json', '--no-download', url],
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
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise RuntimeError("youtube_transcript_api 未安装，请 pip install youtube-transcript-api")

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
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_audio = output_path.replace('.wav', '.tmp')

    result = subprocess.run(
        [YT_DLP, '-f', 'bestaudio/best',
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
    """按文件大小（MB）将 WAV 分割为多个块，返回每块的起始时间戳列表"""
    os.makedirs(chunk_dir, exist_ok=True)
    max_size_bytes = max_size_mb * 1024 * 1024

    # 获取音频总时长
    result = subprocess.run(
        ['/program/tools/ffmpeg', '-i', wav_path],
        capture_output=True, text=True
    )
    m = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})', result.stderr)
    if not m:
        raise RuntimeError("无法获取音频时长")
    total_secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    # 估算每块时长：文件大小 / (采样率 * 通道数 * 字节深度) * 总时长
    # 16kHz mono 16bit = 32000 bytes/s ≈ 0.032 MB/s
    # 所以 max_size_bytes 对应的秒数 ≈ max_size_bytes / 32000
    bytes_per_sec = 16000 * 1 * 2  # 16000Hz * 1ch * 2bytes
    chunk_duration_sec = max_size_bytes / bytes_per_sec

    chunks = []
    start = 0.0
    chunk_idx = 0
    while start < total_secs:
        chunk_path = f"{chunk_dir}/chunk_{chunk_idx:04d}.wav"
        end = min(start + chunk_duration_sec, total_secs)
        duration = end - start

        print(f"  [{chunk_idx:02d}] {start:.1f}s-{end:.1f}s ({duration:.1f}s) → {chunk_path}",
              flush=True)

        subprocess.run([
            FFMPEG, '-i', wav_path,
            '-ss', str(start), '-t', str(duration),
            '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            chunk_path, '-y'
        ], capture_output=True)

        if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 1000:
            chunks.append({'index': chunk_idx, 'start': start, 'end': end, 'path': chunk_path})
        else:
            print(f"  ⚠️ 块 {chunk_idx} 无效或过小，跳过")
        start = end
        chunk_idx += 1

    print(f"  分块完成：共 {len(chunks)} 块")
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
    """删除音频源文件和分块目录"""
    # 删除音频
    if audio_path and os.path.exists(audio_path):
        try:
            os.remove(audio_path)
            print(f"[CLEAN] 删除音频: {audio_path}")
        except Exception as e:
            print(f"[WARN] 删除音频失败: {e}")

    # 删除分块目录
    if os.path.exists(chunk_dir):
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


# ─── 主流程 ───────────────────────────────────────────

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

        # ── 步骤6（前置）：保存音频路径供清理用 ──
        mode = "whisper"
        lang = "zh"

        # ── 步骤7：清理临时文件 ──
        print("[INFO] 清理临时文件...")
        cleanup_audio_and_chunks(audio_path, chunk_dir, video_id)
        if transcript_file and os.path.exists(transcript_file):
            os.remove(transcript_file)
            print(f"[CLEAN] 删除 transcript: {transcript_file}")

    # ── 步骤5：LLM 总结 ──
    print("[INFO] 调用 LLM 生成总结...")
    summary = generate_summary(metadata, full_text, url)

    if not summary:
        print("[WARN] LLM 总结失败")
        summary = f"# {metadata.get('title', '视频总结')}\n\n**LLM 总结生成失败。**\n\n原始文本：\n\n{full_text[:3000]}"

    # ── 步骤6：保存文件 ──
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