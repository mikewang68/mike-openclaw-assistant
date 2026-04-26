# youtube-summarize — YouTube 视频总结 Skill

> 适用场景：YouTube 视频链接，自动抓字幕或转写后生成结构化总结
> 依赖：`youtube-transcript-api`

## 环境说明

| 工具 | 路径 | 状态 |
|------|------|------|
| ffmpeg | `/program/tools/ffmpeg` | ✅ 已安装 |
| Whisper | （待确认 NAS 路径） | ❌ 待配置 |

## 工作流程

### 模式一：有字幕视频（优先）
1. 提取视频 ID
2. 调用 `youtube_transcript_api` 列出可用字幕
3. 获取字幕文本（优先中文 > 英文 > 其他语言）
4. 将字幕内容输出为 JSON（供 Main Agent 总结）

### 模式二：无字幕视频（待实现）
1. 尝试所有可用字幕语言 → 均不可用
2. 用 `yt-dlp` 下载音频（`/program/tools/ffmpeg` 配合）
3. 调用 Whisper 转写（需配置 NAS 路径）
4. 将转写文本输出为 JSON

## 输出格式

```json
{
  "video_id": "...",
  "url": "...",
  "language": "zh",
  "segments": [{"start": 0.0, "timestamp": "00:00:00", "text": "..."}],
  "full_text": "..."
}
```

**注意**：字幕获取成功后输出 JSON，Main Agent 负责调用 LLM 生成结构化总结，并保存到：
`/obsidian/01_Input/01_视频/{YYYY-MM-DD}-youtube-{视频标题}.md`

## 使用方式

发送 YouTube 视频 URL，Skill 自动处理并输出总结文件。
