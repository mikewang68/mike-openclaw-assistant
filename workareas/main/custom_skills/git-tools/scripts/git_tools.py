#!/usr/bin/env python3
"""
git_tools.py - 简化 OpenClaw Agent 的 git 操作

用法：
    python3 git_tools.py status [path]      # 查看状态
    python3 git_tools.py add <path>         # 添加到暂存区
    python3 git_tools.py commit <message>   # 提交
    python3 git_tools.py push               # 推送到远程
    python3 git_tools.py log [n]           # 查看最近n条提交
    python3 git_tools.py diff [path]       # 查看未暂存的变更
    python3 git_tools.py stash              # 暂存当前变更
"""

import subprocess
import sys
import os
import re

REPO_ROOT = "/home/node/.openclaw/workspace"
WORK_DIR = "/home/node/.openclaw/workspace/workareas/main"


def run(cmd, capture=True, check=True):
    """在 workareas/main 目录下执行 git 命令"""
    result = subprocess.run(
        cmd, shell=True, cwd=WORK_DIR,
        capture_output=capture, text=True
    )
    if result.returncode != 0 and check:
        print(f"❌ 命令失败: {cmd}", file=sys.stderr)
        print(f"   {result.stderr.strip()}", file=sys.stderr)
    return result


def get_status_short():
    """获取简短状态"""
    result = run("git status --short")
    if not result.stdout.strip():
        return "✅ 工作区干净，无变更"
    return result.stdout


def cmd_status(path=None):
    """显示变更状态"""
    if path:
        result = run(f"git status --short {path}")
    else:
        result = run("git status --short")
    
    if not result.stdout.strip():
        print("✅ 工作区干净，无变更")
        return
    
    lines = result.stdout.strip().split('\n')
    staged = [l for l in lines if l.startswith(('M', 'A', 'D', 'R', 'C')) and not l.startswith('??')]
    unstaged = [l for l in lines if l.startswith('??')]
    
    if staged:
        print(f"📦 已暂存 ({len(staged)}):")
        for l in staged:
            status, *path_parts = l.split()
            path_str = ' '.join(path_parts)
            print(f"   {status:2s} {path_str}")
        print()
    
    if unstaged:
        print(f"❓ 未跟踪 ({len(unstaged)}):")
        for l in unstaged:
            _, *path_parts = l.split()
            path_str = ' '.join(path_parts)
            print(f"   ?? {path_str}")


def cmd_add(path):
    """添加文件到暂存区"""
    if not path:
        print("❌ 请指定路径", file=sys.stderr)
        return 1
    # 支持相对 workareas/main 的路径或绝对路径
    if path.startswith('/'):
        # 转换为相对路径
        rel = os.path.relpath(path, WORK_DIR)
    else:
        rel = path
    
    result = run(f"git add {rel}")
    if result.returncode == 0:
        print(f"✅ 已添加: {rel}")
        # 显示暂存的变更
        result2 = run(f"git diff --cached --name-only")
        if result2.stdout.strip():
            print(f"📦 当前暂存区 ({len(result2.stdout.strip().splitlines())} 文件):")
            for f in result2.stdout.strip().splitlines()[:10]:
                print(f"   {f}")
            if len(result2.stdout.strip().splitlines()) > 10:
                print(f"   ... 还有 {len(result2.stdout.strip().splitlines())-10} 个文件")
    return result.returncode


def cmd_commit(message):
    """提交"""
    if not message:
        print("❌ 请提供提交信息", file=sys.stderr)
        return 1
    
    result = run(f"git commit -m \"{message}\"")
    if result.returncode == 0:
        output = result.stdout.strip()
        # 提取行数
        m = re.search(r'(\d+) file.*changed', output)
        if m:
            print(f"✅ 提交成功: {output.split(chr(10))[0]}")
        else:
            print(f"✅ {output.split(chr(10))[0] if output else '提交成功'}")
    return result.returncode


def cmd_push():
    """推送到远程"""
    result = run("git push origin main")
    if result.returncode == 0:
        print(f"✅ 已推送到 origin/main")
    return result.returncode


def cmd_log(n=10):
    """查看提交历史"""
    result = run(f"git log --oneline -{n}")
    if result.stdout.strip():
        print(f"📜 最近 {n} 条提交:")
        for line in result.stdout.strip().split('\n'):
            print(f"  {line}")
    return result.returncode


def cmd_diff(path=None):
    """查看未暂存的变更"""
    if path:
        result = run(f"git diff {path}")
    else:
        result = run("git diff")
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("✅ 无未暂存的变更")
    return result.returncode


def cmd_stash():
    """暂存变更"""
    result = run("git stash push -m 'WIP'")
    if result.returncode == 0:
        print(f"✅ 已暂存当前变更: {result.stdout.strip().split(chr(10))[0]}")
    return result.returncode


def cmd_remote():
    """查看远程仓库"""
    result = run("git remote -v")
    if result.stdout.strip():
        for line in result.stdout.strip().split('\n'):
            print(f"  {line}")
    # 不显示完整token
    url_result = run("git config --get remote.origin.url")
    if url_result.stdout.strip():
        url = url_result.stdout.strip()
        # 隐藏token
        hidden = re.sub(r'(https://)[^@]+(@.*)', r'\1***\2', url)
        print(f"\n🔗 {hidden}")
    return result.returncode


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    cmd = sys.argv[1].lower()
    args = sys.argv[2:]
    
    if cmd == 'status' or cmd == 'st':
        cmd_status(*args)
    elif cmd == 'add':
        cmd_add(*args) if args else cmd_status()
    elif cmd == 'commit' or cmd == 'ci':
        cmd_commit(' '.join(args))
    elif cmd == 'push' or cmd == 'p':
        cmd_push()
    elif cmd == 'log' or cmd == 'l':
        cmd_log(int(args[0]) if args else 10)
    elif cmd == 'diff' or cmd == 'd':
        cmd_diff(*args) if args else cmd_diff()
    elif cmd == 'stash':
        cmd_stash()
    elif cmd == 'remote':
        cmd_remote()
    elif cmd == 'help' or cmd == '--help' or cmd == '-h':
        print(__doc__)
    else:
        print(f"❌ 未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
