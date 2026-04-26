#!/usr/bin/env python3
"""
youtube-summarize — YouTube 视频总结脚本

用法：
  python3 summarize.py <YouTube_URL> [--output DIR]

输出：
  1. 优先获取 YouTube 字幕 → Whisper 转写（无字幕时）
  2. 调用 LLM 生成结构化总结
  3. 保存到 --output 指定的目录（默认 /obsidian/01_Input/01_视频/）
  4. 打印 JSON + 保存路径
"""

import sys
import re
import json
import os
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

# ─── 常量 ───────────────────────────────────────────────
FFMPEG   = "/program/tools/ffmpeg"
YT_DLP   = "/home/node/.local/bin/yt-dlp"
WHISPER_API = "http://192.168.1.2:9000/v1/audio/transcriptions"
DEFAULT_OUTPUT = "/obsidian/01_Input/01_视频"
LLM_API_KEY_FILE = "/home/node/.openclaw/agents/main/agent/auth-profiles.json"
AUDIO_DIR = "/obsidian/audio"
TEMP_CHUNKS_DIR = "/obsidian/temp_chunks"


# ─── 工具函数 ───────────────────────────────────────────

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


def format_timestamp(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_llm_api_key() -> str:
    """从 OpenClaw 配置读取 MiniMax API Key"""
    try:
        import json as _json
        with open(LLM_API_KEY_FILE) as f:
            data = _json.load(f)
        for p in data.get('profiles', {}).values():
            if p.get('provider') == 'minimax-cn':
                return p.get('key', '')
    except Exception:
        pass
    return ''


def call_llm(prompt: str, model: str = "MiniMax-M2.7", temperature: float = 0.2) -> str:
    """调用 MiniMax LLM 生成总结"""
    import urllib.request, urllib.error

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


# ─── 视频元数据 ─────────────────────────────────────────

def get_video_metadata(url: str) -> dict:
    """用 yt-dlp 获取视频元数据"""
    try:
        result = subprocess.run(
            [YT_DLP, '--dump-json', '--no-download', url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                'title':       data.get('title', ''),
                'description':  data.get('description', ''),
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


# ─── 字幕获取 ───────────────────────────────────────────

def get_transcript(video_id: str):
    """
    获取 YouTube 字幕，返回 (语言代码, 段落列表)
    段落: [{start, duration, text, timestamp}]
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise RuntimeError("youtube_transcript_api 未安装")

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
            'start':     float(s.start),
            'duration':  float(s.duration),
            'text':      s.text,
            'timestamp':  format_timestamp(float(s.start))
        })
    return target.language_code, segments


# ─── Whisper 转写 ────────────────────────────────────────

def is_whisper_reachable(timeout: int = 5) -> bool:
    """快速检测 Whisper API 是否可用（发送实际请求，timeout内判断）"""
    import requests
    import subprocess, os
    os.makedirs(TEMP_CHUNKS_DIR, exist_ok=True)
    probe_path = f"{TEMP_CHUNKS_DIR}/whisper_probe_{os.getpid()}.wav"
    try:
        subprocess.run([
            FFMPEG, '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono',
            '-t', '1', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            probe_path, '-y'
        ], capture_output=True, timeout=10)
        if not os.path.exists(probe_path):
            return False
        with open(probe_path, 'rb') as f:
            files = {'file': ('probe.wav', f, 'audio/wav')}
            data = {'model': 'whisper-1', 'language': 'zh'}
            resp = requests.post(WHISPER_API, files=files, data=data, timeout=timeout)
            return resp.status_code == 200
    except Exception:
        return False
    finally:
        if os.path.exists(probe_path):
            os.remove(probe_path)


def transcribe_whisper(audio_path: str, timeout: int = 300) -> str:
    """调用 NAS Whisper API 转写音频文件"""
    import requests
    with open(audio_path, 'rb') as f:
        files = {'file': (os.path.basename(audio_path), f, 'audio/wav')}
        data = {'model': 'whisper-1', 'language': 'zh', 'response_format': 'text'}
        resp = requests.post(
            WHISPER_API, files=files, data=data, timeout=timeout
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Whisper API error {resp.status_code}: {resp.text}")
    return resp.text.strip()


def download_audio(url: str, output_path: str) -> str:
    """用 yt-dlp 下载音频并用 ffmpeg 转换为 16kHz WAV"""
    tmp_audio = output_path.replace('.wav', '.audio')

    result = subprocess.run(
        [
            YT_DLP,
            '-f', 'bestaudio/best',
            '-o', tmp_audio + '.%(ext)s',
            '--no-playlist', '--no-post-overwrites',
            url
        ],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr}")

    # 找到生成的音频文件
    found = None
    for ext in ['.webm', '.mp3', '.m4a', '.ogg', '.aac']:
        candidate = tmp_audio + ext
        if os.path.exists(candidate):
            found = candidate
            break
    if not found:
        raise RuntimeError("yt-dlp 未生成音频文件")

    # ffmpeg 转换为 16kHz WAV
    conv = subprocess.run(
        [FFMPEG, '-i', found,
         '-vn', '-acodec', 'pcm_s16le',
         '-ar', '16000', '-ac', '1',
         '-y', output_path],
        capture_output=True, timeout=120
    )
    if found != output_path:
        os.remove(found)
    if not os.path.exists(output_path):
        raise RuntimeError(f"ffmpeg 转换失败: {conv.stderr}")
    return output_path


# ─── LLM 总结生成 ────────────────────────────────────────

def build_summary_prompt(title: str, description: str, channel: str,
                         transcript: str, duration: int) -> str:
    """构建 LLM 总结 prompt"""
    minutes = duration // 60
    date_str = datetime.now().strftime('%Y-%m-%d')

    prompt = f"""你是一个专业的视频内容总结专家。请根据以下YouTube视频信息，生成结构化总结。

## 视频信息
- 标题：{title}
- 频道：{channel}
- 时长：{minutes}分钟
- 日期：{date_str}
- 简介：{description[:500]}

## 字幕/转写内容
{transcript[:8000]}

## 输出要求
请生成以下结构的总结，**严格使用中文**，用 Markdown 格式：

```markdown
# 【视频标题】

- **视频链接**：[URL]
- **频道**：[频道名]
- **时长**：[X]分钟
- **总结日期**：[YYYY-MM-DD]

---

## 视频概要
[2-3句话概括视频核心内容]

---

## 核心内容
[按时间线或主题分段详细总结，每段包含：
- 关键观点/概念
- 重要细节和数据
- 相关参考来源]

---

## 关键概念/术语
[列出视频中出现的核心术语或概念，并简要解释]

---

## 参考来源
[视频中提到的链接、论文、工具等项目]

---

## 个人点评
[对视频内容的评价：价值、局限性、启发等]
```

注意：
1. 如果字幕是英文，请先翻译成中文再总结
2. 要提取视频中的具体信息（人名、机构名、链接、数据）
3. 如果转写质量差（大量无意义文本），请标注"转写质量较差，内容可能不准确"
"""
    return prompt


def generate_summary(metadata: dict, transcript: str) -> str:
    """调用 LLM 生成结构化总结"""
    prompt = build_summary_prompt(
        title       = metadata.get('title', ''),
        description = metadata.get('description', ''),
        channel     = metadata.get('channel', ''),
        transcript  = transcript,
        duration    = metadata.get('duration', 0),
    )
    summary = call_llm(prompt)
    return summary


# ─── 主流程 ─────────────────────────────────────────────

def main():
    url = None
    output_dir = DEFAULT_OUTPUT

    for arg in sys.argv[1:]:
        if arg.startswith('http'):
            url = arg
        elif arg == '--output' or arg == '-o':
            idx = sys.argv.index(arg) + 1
            if idx < len(sys.argv):
                output_dir = sys.argv[idx]

    if not url:
        print("用法: python3 summarize.py <YouTube_URL> [--output DIR]", file=sys.stderr)
        sys.exit(1)

    video_id = extract_video_id(url)
    print(f"[INFO] 视频ID: {video_id}", file=sys.stderr)

    # ── 步骤1：获取视频元数据 ──
    print(f"[INFO] 获取视频元数据...", file=sys.stderr)
    metadata = get_video_metadata(url)

    # ── 步骤2：获取字幕 or Whisper 转写 ──
    mode = "none"
    lang = "unknown"
    full_text = ""
    transcript_error = ""

    # 优先字幕
    try:
        lang, segments = get_transcript(video_id)
        full_text = ' '.join([s['text'] for s in segments])
        mode = "transcript"
        print(f"[INFO] 字幕获取成功，语言: {lang}，字数: {len(full_text)}", file=sys.stderr)
    except Exception as e:
        transcript_error = str(e)
        print(f"[WARN] 字幕获取失败: {e}，尝试 Whisper...", file=sys.stderr)

        # Whisper 转写（先快速检测服务是否可用）
        if is_whisper_reachable(timeout=5):
            os.makedirs(AUDIO_DIR, exist_ok=True)
            audio_wav = f"{AUDIO_DIR}/{video_id}.wav"
            transcript_txt = f"{TEMP_CHUNKS_DIR}/{video_id}_transcript.txt"
            try:
                print(f"[INFO] 下载音频到 {AUDIO_DIR}...", file=sys.stderr)
                download_audio(url, audio_wav)

                # 调用分段转写脚本（进度保存到 TEMP_CHUNKS_DIR）
                whisper_script = os.path.join(os.path.dirname(__file__), 'whisper_transcribe.py')
                print(f"[INFO] Whisper 分段转写中（请耐心）...", file=sys.stderr)
                result = subprocess.run(
                    [sys.executable, whisper_script, audio_wav, transcript_txt],
                    capture_output=True, text=True, timeout=3600
                )
                if result.returncode == 0 and os.path.exists(transcript_txt):
                    with open(transcript_txt) as f:
                        full_text = f.read().strip()
                    if full_text:
                        mode = "whisper"
                        lang = "zh"
                        print(f"[INFO] Whisper 转写成功，字数: {len(full_text)}", file=sys.stderr)
                    else:
                        raise ValueError("转写结果为空")
                else:
                    raise RuntimeError(result.stderr or result.stdout or "转写脚本失败")
            except Exception as e2:
                print(f"[WARN] Whisper 转写失败: {e2}，使用元数据总结", file=sys.stderr)
                transcript_error += f" | Whisper失败: {e2}"
                mode = "metadata_only"
                full_text = metadata.get('description', '') or metadata.get('title', '')
        else:
            print(f"[WARN] Whisper API 不可用（192.168.1.2:9000 无响应），跳过转写", file=sys.stderr)
            transcript_error = "Whisper API 不可用"
            mode = "metadata_only"
            full_text = metadata.get('description', '') or metadata.get('title', '')

        # 清理临时音频文件
        try:
            if 'audio_wav' in dir() and os.path.exists(audio_wav):
                os.remove(audio_wav)
                print(f"[INFO] 已删除临时音频: {audio_wav}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] 删除音频失败: {e}", file=sys.stderr)

    # ── 步骤3：LLM 总结 ──
    print(f"[INFO] 调用 LLM 生成总结...", file=sys.stderr)
    summary = generate_summary(metadata, full_text)

    if not summary:
        print("[WARN] LLM 总结失败，仅保存原始数据", file=sys.stderr)
        summary = f"**LLM 总结生成失败。**\n\n原始字幕/文本：\n\n{full_text[:3000]}"

    # ── 步骤4：保存文件 ──
    date_str = datetime.now().strftime('%Y-%m-%d')
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', metadata.get('title', 'untitled'))[:50]
    filename = f"{date_str}-youtube-{safe_title}.md"
    filepath = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(summary)

    print(f"[INFO] 已保存: {filepath}", file=sys.stderr)

    # ── 步骤5：清理临时 chunk 文件 ──
    try:
        import glob
        for f in glob.glob(f"{TEMP_CHUNKS_DIR}/chunk_*.wav") + glob.glob(f"{TEMP_CHUNKS_DIR}/chunk_*.wav.*"):
            try:
                os.remove(f)
            except:
                pass
        for f in glob.glob(f"{TEMP_CHUNKS_DIR}/*_progress.json"):
            try:
                os.remove(f)
            except:
                pass
    except Exception as e:
        print(f"[WARN] 清理chunk文件失败: {e}", file=sys.stderr)

    # ── 步骤6：输出 JSON ──
    result = {
        "video_id":    video_id,
        "url":         url,
        "mode":        mode,
        "language":    lang,
        "title":       metadata.get('title', ''),
        "description": metadata.get('description', ''),
        "duration":    metadata.get('duration', 0),
        "channel":     metadata.get('channel', ''),
        "tags":        metadata.get('tags', []),
        "transcript":  full_text[:500] if full_text else '',
        "summary_file": filepath,
        "transcript_file": transcript_txt if mode == "whisper" else None,
        "transcript_error": transcript_error if transcript_error else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
