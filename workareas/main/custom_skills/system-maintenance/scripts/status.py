#!/usr/bin/env python3
"""
system_maintenance_status.py — 系统维护状态概览
"""
import os, subprocess
from datetime import datetime

WORKSPACE = "/home/node/.openclaw/workspace/workareas/main"
MEMORY_DIR = os.path.join(WORKSPACE, "memory")
ARCHIVE_DIR = os.path.join(WORKSPACE, ".session_archive")
SESSION_DIR = "/home/node/.openclaw/sessions"

SCRIPTS = {
    "每日任务检查": "/home/node/.openclaw/workspace/skills/auto-memory/check_and_update.py",
    "每周记忆整合": "/home/node/.openclaw/workspace/skills/auto-memory/weekly_consolidate.py",
    "Session压缩": "/home/node/.openclaw/workspace/session_clean.py",
}

LOG_FILE = "/home/node/.openclaw/workspace/workareas/shared/cleanup.log"


def check_script(name, path):
    if os.path.exists(path):
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        size = os.path.getsize(path)
        return f"✅ {name} — {size} bytes — {mtime.strftime('%Y-%m-%d %H:%M')}"
    else:
        return f"❌ {name} — 文件不存在！"


def check_cleanup_log():
    if not os.path.exists(LOG_FILE):
        return "  日志文件不存在"
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
        if lines:
            last = lines[-1].strip()
            return f"  最新: {last[:100]}"
    except:
        pass
    return "  无法读取"


def check_archive():
    if not os.path.exists(ARCHIVE_DIR):
        return "  无归档目录"
    files = os.listdir(ARCHIVE_DIR)
    if not files:
        return "  归档目录为空"
    files.sort(reverse=True)
    return f"  最新归档: {files[-1][:50]}"


def check_recent_md():
    fpath = os.path.join(WORKSPACE, "RECENT.md")
    if not os.path.exists(fpath):
        return "❌ RECENT.md 不存在"
    mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
    today = datetime.now().date()
    if mtime.date() == today:
        return f"✅ RECENT.md 已更新（今天 {mtime.strftime('%H:%M')}）"
    else:
        return f"⚠️ RECENT.md 未更新（上次: {mtime.strftime('%Y-%m-%d %H:%M')}）"


def check_memory():
    if not os.path.exists(MEMORY_DIR):
        return "❌ memory 目录不存在"
    files = [f for f in os.listdir(MEMORY_DIR) if f.startswith("20")]
    if not files:
        return "⚠️ 无 memory 文件"
    files.sort(reverse=True)
    return f"  最新: {files[-1]}（{len(files)} 个文件）"


def check_sessions():
    agents_dir = "/home/node/.openclaw/agents"
    if not os.path.exists(agents_dir):
        return "❌ agents 目录不存在"
    total_files = 0
    total_size = 0
    for agent in os.listdir(agents_dir):
        sess_dir = os.path.join(agents_dir, agent, "sessions")
        if os.path.exists(sess_dir):
            for f in os.listdir(sess_dir):
                fpath = os.path.join(sess_dir, f)
                if os.path.isfile(fpath):
                    total_files += 1
                    total_size += os.path.getsize(fpath)
    if total_files == 0:
        return "  无 session 文件"
    return f"  {total_files} 个 session文件，{total_size/1024/1024:.1f}MB"


def main():
    print("=" * 55)
    print("系统维护状态")
    print("=" * 55)

    print("\n📜 脚本文件:")
    for name, path in SCRIPTS.items():
        print(f"  {check_script(name, path)}")

    print(f"\n🧹 cleanup.log:")
    print(check_cleanup_log())

    print(f"\n📁 归档目录:")
    print(check_archive())

    print(f"\n📋 RECENT.md:")
    print(f"  {check_recent_md()}")

    print(f"\n🧠 memory:")
    print(f"  {check_memory()}")

    print(f"\n💾 sessions:")
    print(f"  {check_sessions()}")

    print()


if __name__ == "__main__":
    main()
