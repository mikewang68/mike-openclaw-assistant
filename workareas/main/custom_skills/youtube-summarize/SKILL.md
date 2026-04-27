# youtube-summarize — YouTube 视频总结 Skill

> 适用场景：YouTube 视频链接，自动抓字幕或转写后生成结构化总结
> 依赖：`youtube-transcript-api`、`yt-dlp`、`ffmpeg`（/program/tools/ffmpeg）、Whisper（NAS 192.168.1.2:9000）

## 环境状态

| 工具 | 路径 | 状态 |
|------|------|------|
| ffmpeg | `/program/tools/ffmpeg` | ✅ 已安装 |
| yt-dlp | `/home/node/.local/bin/yt-dlp` | ✅ 已安装 |
| Whisper | `192.168.1.2:9000/v1/audio/transcriptions` | ✅ 可用 |
| youtube-transcript-api | pip 安装 | ✅ 已安装 |

## 工作流程

### 模式一：有字幕视频
1. 提取视频 ID
2. 调用 `youtube_transcript_api` 获取字幕（优先 zh > en）
3. 字幕文本直接送 LLM 生成结构化总结
4. 保存到 `/obsidian/01_Input/01_视频/{date}-youtube-{标题}.md`

### 模式二：无字幕视频（Whisper 转写）
1. 下载音视频 → `/obsidian/audio/{video_id}.wav`
2. 按 **10MB** 分块 → `/obsidian/temp_chunks/{video_id}_chunks/chunk_0000.wav`
3. 逐块送 Whisper（`Systran/faster-whisper-tiny`）转写为文本
4. 合并所有块的文本 → LLM 生成结构化总结
5. 保存总结文件
6. **删除** `/obsidian/audio/{video_id}.wav` 和 `/obsidian/temp_chunks/{video_id}_chunks/` 下所有文件

## Whisper 配置

- **API**：`http://192.168.1.2:9000/v1/audio/transcriptions`
- **模型**：`Systran/faster-whisper-tiny`（可选：tiny/base/small/large-v3）
- **单块超时**：300 秒
- **语言**：中文优先（`language=zh`）
- **格式**：16kHz mono WAV

## 分块策略

按文件大小分割（10MB/块），而非按时间分割，保证每块大小均匀。
估算方法：16kHz mono 16bit ≈ 0.032 MB/s

## 输出

- 总结文件：`/obsidian/01_Input/01_视频/{YYYY-MM-DD}-youtube-{视频标题}.md`
- JSON 包含：`video_id`、`mode`（transcript/whisper）、`transcript_chars`、`summary_file`

## 使用方式

发送 YouTube 视频 URL，Skill 自动处理全流程（下载→分块→转写→总结→清理）。