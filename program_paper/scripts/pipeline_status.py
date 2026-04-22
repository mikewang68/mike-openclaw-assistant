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

def get_paper_state(paper_id):
    """读取指定论文的状态（从status.json）"""
    s = load_status()
    papers = s.get("papers", {})
    return papers.get(paper_id, {})

def set_paper_state(paper_id, state=None, step=None, retry=0, reason=None):
    """更新指定论文的状态到status.json"""
    s = load_status()
    papers = s.get("papers", {})
    
    if paper_id not in papers:
        papers[paper_id] = {"status": "pending", "reviewer_done": False, "coach_done": False, "code_done": False, "retries": 0}
    
    ps = papers[paper_id]
    if state is not None:
        ps["status"] = state
    if step is not None:
        ps["current_step"] = step
    if retry is not None:
        ps["retries"] = retry
    if reason is not None:
        ps["reason"] = reason
    ps["updated_at"] = datetime.now().isoformat()
    
    s["papers"] = papers
    s["last_heartbeat"] = datetime.now().isoformat()
    save_status(s)
    return ps

def list_papers_by_status(status_filter=None, step_filter=None):
    """列出指定状态的论文"""
    s = load_status()
    papers = s.get("papers", {})
    result = []
    for pid, ps in papers.items():
        if status_filter and ps.get("status") != status_filter:
            continue
        if step_filter and ps.get("current_step") != step_filter:
            continue
        result.append((pid, ps))
    return result

def get_pending_reviewers(limit=3):
    """获取待处理reviewer的论文（最多返回limit个）"""
    s = load_status()
    passed = s.get("papers_passed", [])
    papers = s.get("papers", {})
    
    pending = []
    for pid in passed:
        ps = papers.get(pid, {})
        if ps.get("status") == "pending" or ps.get("current_step") == "reviewer":
            pending.append(pid)
        if len(pending) >= limit:
            break
    return pending[:limit]

def update_counts():
    """根据papers字典统计计数并更新到顶层字段"""
    s = load_status()
    papers = s.get("papers", {})
    
    total = len(s.get("papers_passed", []))
    completed = sum(1 for ps in papers.values() if ps.get("status") == "completed")
    failed = sum(1 for ps in papers.values() if ps.get("status") in ("failed", "killed"))
    skipped = sum(1 for ps in papers.values() if ps.get("status") == "score_too_low")
    
    s["papers_total"] = total
    s["papers_completed"] = completed
    s["papers_failed"] = failed
    s["papers_skipped"] = skipped
    save_status(s)
    return total, completed, failed, skipped

def pass_papers(paper_ids):
    """记录通过Phase1b的论文列表（初始化papers字典）"""
    s = load_status()
    s["papers_passed"] = paper_ids
    # 初始化每篇论文状态
    papers = {}
    for pid in paper_ids:
        papers[pid] = {
            "status": "pending",
            "reviewer_done": False,
            "coach_done": False,
            "code_done": False,
            "retries": 0,
            "current_step": None,
            "updated_at": datetime.now().isoformat()
        }
    s["papers"] = papers
    save_status(s)
    return len(paper_ids)
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
        
    elif cmd == "pass_papers":
        # pass_papers: 从 passed.json 初始化论文状态
        import sys; sys.path.insert(0, '/program/paper/scripts')
        date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")
        base = f"/home/node/.openclaw/workspace/workareas/shared/papers/{date_str}"
        passed_path = os.path.join(base, "passed.json")
        if os.path.exists(passed_path):
            with open(passed_path) as f:
                passed_list = json.load(f)
            paper_ids = [p['arxiv_id'].split('v')[0] for p in passed_list]
            result = pass_papers(paper_ids)
            print(f"✅ Initialized {result} papers for {date_str}")
        else:
            print(f"⚠️  passed.json not found at {passed_path}")
            sys.exit(1)
    
    elif cmd == "pending":
        # pending: 列出待处理论文
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        pending = get_pending_reviewers(limit=limit)
        for pid in pending:
            print(pid)
    
    elif cmd == "set_paper":
        # set_paper <arxiv_id> <state> [step] [retry]
        paper_id = sys.argv[2] if len(sys.argv) > 2 else None
        state = sys.argv[3] if len(sys.argv) > 3 else None
        step = sys.argv[4] if len(sys.argv) > 4 else None
        retry = int(sys.argv[5]) if len(sys.argv) > 5 else 0
        if paper_id and state:
            ps = set_paper_state(paper_id, state=state, step=step, retry=retry)
            print(f"✅ {paper_id}: {state} (step={step})")
        else:
            print("Usage: set_paper <arxiv_id> <state> [step] [retry]")
            sys.exit(1)
    
    elif cmd == "counts":
        total, completed, failed, skipped = update_counts()
        print(f"Total: {total}, Completed: {completed}, Failed: {failed}, Skipped: {skipped}")
    
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: pipeline_status.py [start|complete|crash|heartbeat|phase1a|phase1b|phase2a|phase2b|update|status|pass_papers|pending|set_paper|counts]")
        sys.exit(1)
