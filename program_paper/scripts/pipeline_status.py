#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Status Manager - 全局Pipeline状态写入
所有状态更新都通过这个脚本，保证原子性。

路径：/home/node/.openclaw/workspace/workareas/shared/papers/_pipeline_status.json
"""

import json
import os
import sys
from datetime import datetime

STATUS_FILE = "/home/node/.openclaw/workspace/workareas/shared/papers/_pipeline_status.json"

def load_status():
    """读取当前状态，如果文件不存在则返回默认值"""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "pipeline": "workflow_paper_arxiv.md",
        "status": "idle",
        "phase": None,
        "date": None,
        "started_at": None,
        "last_heartbeat": None,
        "papers_total": 0,
        "papers_completed": 0,
        "papers_failed": 0,
        "papers_skipped": 0,
        "error": None,
        "crash_reason": None,
        "session_id": None
    }

def save_status(status):
    """原子写入状态文件"""
    # 写入临时文件再rename，保证原子性
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATUS_FILE)

def update_status(
    status_value,
    phase=None,
    papers_total=None,
    papers_completed=None,
    papers_failed=None,
    papers_skipped=None,
    error=None,
    crash_reason=None,
    session_id=None
):
    """更新状态字段，只更新非None的值"""
    s = load_status()
    
    s["status"] = status_value
    s["last_heartbeat"] = datetime.now().isoformat()
    
    if phase is not None:
        s["phase"] = phase
    if papers_total is not None:
        s["papers_total"] = papers_total
    if papers_completed is not None:
        s["papers_completed"] = papers_completed
    if papers_failed is not None:
        s["papers_failed"] = papers_failed
    if papers_skipped is not None:
        s["papers_skipped"] = papers_skipped
    if error is not None:
        s["error"] = error
    if crash_reason is not None:
        s["crash_reason"] = crash_reason
    if session_id is not None:
        s["session_id"] = session_id
        
    save_status(s)
    return s

def start_pipeline(date_str, session_id=None):
    """Pipeline开始运行"""
    s = load_status()
    
    # 如果已经是running状态，说明上次pipeline还没结束
    if s["status"] == "running":
        s["status"] = "crashed"
        s["crash_reason"] = "Previous pipeline was still running when new one started"
        save_status(s)
    
    s = {
        "pipeline": "workflow_paper_arxiv.md",
        "status": "running",
        "phase": "phase1",
        "date": date_str,
        "started_at": datetime.now().isoformat(),
        "last_heartbeat": datetime.now().isoformat(),
        "papers_total": 0,
        "papers_completed": 0,
        "papers_failed": 0,
        "papers_skipped": 0,
        "error": None,
        "crash_reason": None,
        "session_id": session_id
    }
    save_status(s)
    return s

def complete_pipeline():
    """Pipeline正常完成"""
    s = load_status()
    s["status"] = "completed"
    s["phase"] = "done"
    s["last_heartbeat"] = datetime.now().isoformat()
    save_status(s)
    return s

def crash_pipeline(reason):
    """Pipeline崩溃"""
    s = load_status()
    s["status"] = "crashed"
    s["crash_reason"] = reason
    s["last_heartbeat"] = datetime.now().isoformat()
    save_status(s)
    return s

def get_heartbeat_age():
    """返回last_heartbeat距离现在多少秒"""
    s = load_status()
    if not s["last_heartbeat"]:
        return 999999
    try:
        last = datetime.fromisoformat(s["last_heartbeat"])
        return (datetime.now() - last).total_seconds()
    except Exception:
        return 999999

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    
    if cmd == "start":
        date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")
        session_id = sys.argv[3] if len(sys.argv) > 3 else None
        result = start_pipeline(date_str, session_id)
        print(f"✅ Pipeline started: {date_str}")
        
    elif cmd == "complete":
        result = complete_pipeline()
        print(f"✅ Pipeline completed")
        
    elif cmd == "crash":
        reason = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        result = crash_pipeline(reason)
        print(f"🔴 Pipeline crashed: {reason}")
        
    elif cmd == "heartbeat":
        result = update_status("running")
        print(f"💓 Heartbeat updated")
        
    elif cmd == "phase1a":
        result = update_status("running", phase="phase1a")
        print(f"📡 Phase1a started")
        
    elif cmd == "phase1b":
        result = update_status("running", phase="phase1b")
        print(f"📊 Phase1b started")
        
    elif cmd == "phase2a":
        result = update_status("running", phase="phase2a")
        print(f"📥 Phase2a started")
        
    elif cmd == "phase2b":
        result = update_status("running", phase="phase2b")
        print(f"⚙️ Phase2b started")
        
    elif cmd == "update":
        # update papers counts: python3 pipeline_status.py update --total 50 --completed 10 --failed 2 --skipped 38
        kwargs = {}
        for i in range(2, len(sys.argv)):
            arg = sys.argv[i]
            if arg.startswith("--"):
                key = arg[2:]
                if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                    val = sys.argv[i + 1]
                    if key == "total":
                        kwargs["papers_total"] = int(val)
                    elif key == "completed":
                        kwargs["papers_completed"] = int(val)
                    elif key == "failed":
                        kwargs["papers_failed"] = int(val)
                    elif key == "skipped":
                        kwargs["papers_skipped"] = int(val)
        result = update_status("running", **kwargs)
        print(f"📝 Status updated: {kwargs}")
        
    elif cmd == "crashed_check":
        # 用于watchdog：检查是否崩溃
        age = get_heartbeat_age()
        s = load_status()
        if s["status"] == "crashed":
            print(f"🔴 CRASHED: {s.get('crash_reason', 'unknown')}")
            sys.exit(2)
        elif s["status"] == "running" and age > 900:
            # 15分钟无心跳
            crash_pipeline(f"No heartbeat for {age:.0f}s (>15min)")
            print(f"🔴 HEARTBEAT_TIMEOUT: {age:.0f}s")
            sys.exit(2)
        elif s["status"] == "running":
            print(f"✅ Alive: {age:.0f}s ago, phase={s.get('phase')}, papers={s.get('papers_completed',0)}/{s.get('papers_total',0)}")
            sys.exit(0)
        else:
            print(f"ℹ️  Status: {s['status']}")
            sys.exit(0)
            
    elif cmd == "heartbeat_age":
        # 返回心跳年龄（秒），供调度循环判断
        age = get_heartbeat_age()
        print(f"{age:.0f}")
        sys.exit(0 if age <= 900 else 1)
            
    elif cmd == "status":
        s = load_status()
        print(json.dumps(s, indent=2, ensure_ascii=False))
        
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: pipeline_status.py [start|complete|crash|heartbeat|phase1a|phase1b|phase2a|phase2b|update|status|crashed_check]")
        sys.exit(1)
