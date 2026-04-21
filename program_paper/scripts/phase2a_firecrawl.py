#!/usr/bin/env python3
"""Phase2a 批量下载论文全文（Firecrawl - PDF URL）"""
import json, urllib.request, urllib.error, time, os, sys
from datetime import datetime

DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y-%m-%d')
BASE = f'/home/node/.openclaw/workspace/workareas/shared/papers/{DATE}'
PAPERS_JSON = f'{BASE}/passed.json'
FC_URL = 'http://192.168.1.2:8080/v1/scrape'
DELAY = 4  # 每篇间隔4秒

def download_one(aid):
    paper_dir = os.path.join(BASE, aid)
    os.makedirs(paper_dir, exist_ok=True)
    md_path = os.path.join(paper_dir, 'paper.md')

    # 已有且 > 5000 字（完整PDF内容）则跳过
    if os.path.exists(md_path) and os.path.getsize(md_path) > 5000:
        return 'skipped', aid, '已有完整内容'

    # PDF URL
    url = f'https://arxiv.org/pdf/{aid}.pdf'
    payload = json.dumps({'url': url, 'formats': ['markdown']}).encode('utf-8')

    try:
        req = urllib.request.Request(
            FC_URL,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode('utf-8'))
        content = d.get('data', {}).get('markdown', '')
        if content and len(content) > 1000:
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return 'success', aid, f'{len(content)}字'
        else:
            return 'short', aid, f'内容{len(content)}字'
    except Exception as e:
        return 'fail', aid, str(e)[:80]

def main():
    if not os.path.exists(PAPERS_JSON):
        print(f"错误：{PAPERS_JSON} 不存在")
        sys.exit(1)

    with open(PAPERS_JSON) as f:
        passed = json.load(f)

    papers = []
    for p in passed:
        aid = p['arxiv_id'].split('v')[0].rstrip('.')
        papers.append({'arxiv_id': aid, 'title': p.get('title', '')[:50]})

    total = len(papers)
    print(f'Phase2a 开始下载 {total} 篇论文全文（Firecrawl PDF）')
    print(f'日期：{DATE}')
    print(f'开始时间：{datetime.now().strftime("%H:%M:%S")}')
    print('=' * 50)

    stats = {'success': 0, 'skipped': 0, 'short': 0, 'fail': 0}

    for i, p in enumerate(papers, 1):
        aid = p['arxiv_id']
        status, aid_out, detail = download_one(aid)
        stats[status] += 1

        icon = {'success': 'OK', 'skipped': 'SKIP', 'short': 'SHORT', 'fail': 'FAIL'}[status]
        print(f'[{i}/{total}] {icon} {aid_out} | {detail}')

        if status == 'success':
            time.sleep(DELAY)

    print('=' * 50)
    print(f'完成：成功{stats["success"]} | 跳过{stats["skipped"]} | 过短{stats["short"]} | 失败{stats["fail"]}')

if __name__ == '__main__':
    main()