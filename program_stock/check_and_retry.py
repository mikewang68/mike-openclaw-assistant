#!/usr/bin/env python3
"""
每日股票数据流水线 — 补位检测与执行脚本
由 cron job 调用，替代 LLM agent 判断

逻辑：
1. 读取上一次 16:05 执行的 cron runs JSONL
2. 判断是否为幽灵运行（status=ok 但 duration < 30000ms 或 delivered=false）
3. 如果是幽灵运行 → 执行 daily_sync.py + audit_monitor.py
4. 写入审计日志
"""

import json
import os
import sys
import sqlite3
import subprocess
from datetime import datetime

WORK_DIR = "/program/stock"
CRON_RUNS_JSONL = "/home/node/.openclaw/cron/runs/1e88f7ed-c229-44b0-a873-c24cd3ad786c.jsonl"
STOCK_PIPELINE_JOB_ID = "1e88f7ed-c229-44b0-a873-c24cd3ad786c"
LOG_FILE = "/home/node/.openclaw/workspace/workareas/quant/audit/retry_log.txt"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"  (log write failed: {e})")


def get_last_cron_run():
    """读取上一次 16:05 股票流水线的执行结果"""
    if not os.path.exists(CRON_RUNS_JSONL):
        return None
    
    with open(CRON_RUNS_JSONL) as f:
        lines = f.readlines()
    
    # 找最新的 finished 行
    for line in reversed(lines):
        try:
            entry = json.loads(line.strip())
            if entry.get("jobId") == STOCK_PIPELINE_JOB_ID and entry.get("action") == "finished":
                return entry
        except json.JSONDecodeError:
            continue
    return None


def is_ghost_run(run_entry):
    """判断是否为幽灵运行"""
    if not run_entry:
        return True, "无执行记录"
    
    status = run_entry.get("status", "")
    duration = run_entry.get("durationMs", 0)
    delivered = run_entry.get("delivered", False)
    
    # 幽灵运行特征：status=ok 但 duration 极短 或 未送达
    if status == "ok" and (duration < 30000 or not delivered):
        if duration < 30000:
            return True, f"幽灵运行：status=ok 但 duration={duration}ms < 30000ms"
        else:
            return True, f"未送达：delivered={delivered}, duration={duration}ms"
    
    if status != "ok":
        return True, f"status={status}（异常）"
    
    return False, f"正常：status=ok, duration={duration}ms, delivered={delivered}"


def run_python_script(script_name):
    """执行 Python 脚本，返回 (success, output)"""
    script_path = os.path.join(WORK_DIR, script_name)
    if not os.path.exists(script_path):
        return False, f"脚本不存在: {script_path}"
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "/home/node/.local/lib/python3.11/site-packages"
    
    log(f"执行: python3 {script_name}")
    try:
        result = subprocess.run(
            ["python3", script_name],
            cwd=WORK_DIR,
            capture_output=True,
            text=True,
            timeout=600,
            env=env
        )
        output = result.stdout.strip() if result.stdout else result.stderr.strip()
        success = result.returncode == 0
        log(f"  → 返回码: {result.returncode}")
        if output:
            for line in output.split("\n")[-10:]:  # 只保留最后10行
                log(f"     {line}")
        return success, output
    except subprocess.TimeoutExpired:
        return False, f"脚本执行超时（600秒）"
    except Exception as e:
        return False, f"执行异常: {e}"


def main():
    log("=" * 50)
    log("每日股票数据流水线 — 补位检测开始")
    
    # Step 1: 检测上一次执行
    last_run = get_last_cron_run()
    if last_run:
        log(f"上一次执行记录: runAtMs={last_run.get('runAtMs')}, status={last_run.get('status')}, "
            f"duration={last_run.get('durationMs')}ms, delivered={last_run.get('delivered')}")
    
    is_ghost, reason = is_ghost_run(last_run)
    log(f"幽灵运行判断: {reason}")
    
    if not is_ghost:
        log("上一次执行正常，无需补救，退出。")
        sys.exit(0)
    
    # Step 2: 执行补救
    log("开始执行补救...")
    
    # 确保 pip 可用
    try:
        import pip
    except ImportError:
        log("安装 pip...")
        subprocess.run(
            ["curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && python3 /tmp/get-pip.py --break-system-packages"],
            shell=True, capture_output=True
        )
    
    # 安装依赖（如需要）
    req_file = os.path.join(WORK_DIR, "requirements.txt")
    if os.path.exists(req_file):
        log("检查依赖...")
        try:
            subprocess.run(
                ["python3", "-m", "pip", "install", "-r", req_file, "--break-system-packages", "-q"],
                capture_output=True, timeout=120
            )
        except Exception as e:
            log(f"  依赖安装异常: {e}")
    
    # Step 3: 执行 daily_sync.py
    success1, _ = run_python_script("daily_sync.py")
    if not success1:
        log("daily_sync.py 执行失败，跳过后续步骤。")
        sys.exit(1)
    
    # Step 4: 执行 audit_monitor.py
    success2, _ = run_python_script("audit_monitor.py")
    if not success2:
        log("audit_monitor.py 执行失败。")
        sys.exit(1)
    
    log("补救执行完成！")
    sys.exit(0)


if __name__ == "__main__":
    main()
