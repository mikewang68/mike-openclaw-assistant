import sys
import json
import urllib.request
import urllib.error
import subprocess
import os
import re
import time


class UrlopenRetry:
    """urllib.urlopen wrapper with 429 exponential backoff retry.
    Usage: with UrlopenRetry(req) as response:
    """
    def __init__(self, req, max_retries=5):
        self.req = req
        self.max_retries = max_retries
        self.response = None

    def __enter__(self):
        for attempt in range(self.max_retries):
            try:
                self.response = urllib.request.urlopen(self.req)
                return self.response
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.max_retries - 1:
                    wait = 30 * (2 ** attempt)
                    print(f"     [Feishu 429] waiting {wait}s (attempt {attempt+1}/{self.max_retries})", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise
        return self.response

    def __exit__(self, *args):
        if self.response:
            self.response.close()

def get_tenant_access_token(app_id, app_secret):
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    with UrlopenRetry(req) as response:
        res = json.loads(response.read().decode('utf-8'))
        return res.get("tenant_access_token")

def upload_file(file_path, token, app_token):
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    cmd = [
        "curl", "-s", "-X", "POST", "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
        "-H", f"Authorization: Bearer {token}",
        "-F", f"file_name={file_name}",
        "-F", "parent_type=bitable_file",
        "-F", f"parent_node={app_token}",
        "-F", f"size={file_size}",
        "-F", f"file=@{file_path}"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        res_json = json.loads(res.stdout)
        if res_json.get("code") == 0:
            return res_json["data"]["file_token"]
        else:
            print(f"Feishu file upload failed: {res_json}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"Error parsing upload response: {res.stdout}", file=sys.stderr)
        return None

def find_record_by_title(token, app_token, table_id, title):
    """Find existing record by exact title prefix match. Returns (record_id, fields) or (None, None)."""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    data = json.dumps({
        "filter": {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": "题目",
                    "operator": "contains",
                    "value": title[:80]
                }
            ]
        },
        "page_size": 20
    }).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }, method="POST")
    try:
        with UrlopenRetry(req) as response:
            res = json.loads(response.read().decode('utf-8'))
            if res.get("code") == 0:
                items = res.get("data", {}).get("items", [])
                for item in items:
                    fields = item.get("fields", {})
                    rec_title = fields.get("题目", "")
                    if rec_title.lower().startswith(title[:80].lower()):
                        return item.get("record_id"), fields
            return None, None
    except Exception as e:
        print(f"Search error: {e}", file=sys.stderr)
        return None, None

def update_record(token, app_token, table_id, record_id, fields):
    """Update fields on an existing record."""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    data = json.dumps({"fields": fields}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }, method="PUT")
    try:
        with UrlopenRetry(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8')
        print(f"Update error: {error_msg}", file=sys.stderr)
        return {"code": -1, "msg": error_msg}

def insert_record(token, app_token, table_id, fields):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    data = json.dumps({"fields": fields}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }, method="POST")
    try:
        with UrlopenRetry(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8')
        print(f"Feishu API error: {error_msg}", file=sys.stderr)
        sys.exit(1)

def extract_arxiv_id(md_path, md_content):
    """Extract arXiv ID from markdown path, source field, or content."""
    # 1. Try to extract from markdown file path: YYYY-MM-DD-title-评阅意见.md (新格式，无arXivID)
    m = re.search(r'(260[1-9]\.\d{5})', md_path)
    if m:
        return m.group(1)
    # 2. Try standard content patterns
    for pat in [r'\[\[(\d{4}\.\d{5})[^\]]*\]\]', r'arXiv:\s*(\d{4}\.\d{5})', r'arxiv\.org/abs/(\d{4}\.\d{5})']:
        m = re.search(pat, md_content)
        if m:
            return m.group(1)
    return None

def parse_markdown_to_fields(md_content):
    fields = {}
    
    # Title: 兼容 **Title**: 和 Title: 两种格式
    title_match = re.search(r'\*\*Title\*\*:\s*(.+)|^[Tt]itle:\s*(.+)', md_content, re.MULTILINE)
    if title_match: fields['题目'] = (title_match.group(1) or title_match.group(2)).strip()
        
    # Tags: 兼容 **Tags**: 和 Tags: 两种格式
    tags_match = re.search(r'\*\*Tags\*\*:\s*(.+)|^[Tt]ags:\s*(.+)', md_content, re.MULTILINE)
    if tags_match: 
        tag_str = (tags_match.group(1) or tags_match.group(2)).strip()
        fields['研究方向'] = [tag_str.strip('[]').strip()]
        
    # Keywords: 兼容 **Keywords**: 和 Keywords: 两种格式
    kw_match = re.search(r'\*\*Keywords\*\*:\s*(.+)|^[Kk]eywords:\s*(.+)', md_content, re.MULTILINE)
    if kw_match: fields['关键词'] = f"[{(kw_match.group(1) or kw_match.group(2)).strip()}]"
        
    # Date: 兼容 **Date**: 和 Date: 两种格式
    date_match = re.search(r'\*\*Date\*\*:\s*(.+)|^[Dd]ate:\s*(.+)', md_content, re.MULTILINE)
    if date_match: fields['日期'] = (date_match.group(1) or date_match.group(2)).strip()
        
    def extract_field(field_name, text):
        # 兼容 **Field**: 和 Field: 两种格式
        match = re.search(rf'\*\* ?{field_name} ?\*\*[：:]\s*(.*?)(?=\n\s*##|\n[^ \t]|\Z)|^{field_name}[：:]\s*(.*?)(?=\n\s*##|\n[^ \t]|\Z)', text, re.DOTALL | re.MULTILINE)
        if match:
            return (match.group(1) or match.group(2) or '').strip()
        return ''

    fields['单位'] = extract_field('单位', md_content)
    fields['作者'] = f"[{extract_field('作者', md_content)}]"
    fields['级别'] = extract_field('级别', md_content) or extract_field('适合投稿期刊的级别', md_content)
    fields['论文摘要'] = extract_field('论文摘要', md_content)
    fields['领域'] = extract_field('聚焦领域', md_content)
    fields['解决问题'] = extract_field('聚焦问题', md_content)
    fields['解决方法和技术路线'] = extract_field('解决方法和技术路线', md_content)
    fields['实验设计'] = extract_field('实验设计', md_content)
    fields['实验结果'] = extract_field('实验结果', md_content)
    fields['创新点'] = extract_field('创新贡献', md_content)
    fields['不足'] = extract_field('不足之处', md_content)
    fields['期刊/会议'] = extract_field('投稿期刊的建议', md_content)
    fields['总结'] = extract_field('总结', md_content)
    
    fields['创建人'] = "OpenClaw"
    
    return fields

def main():
    if len(sys.argv) < 3:
        print("Usage: python sync_feishu.py <markdown_file_path> <pdf_file_path>", file=sys.stderr)
        sys.exit(1)

    md_path = sys.argv[1]
    pdf_path = sys.argv[2]

    if not os.path.exists(md_path):
        print(f"Markdown file not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    with open(md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Extract arXiv ID for deduplication (from path first, then content)
    arxiv_id = extract_arxiv_id(md_path, md_content)
    print(f"Extracted arXiv ID: {arxiv_id}")

    fields = parse_markdown_to_fields(md_content)

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)

    token = get_tenant_access_token(config["app_id"], config["app_secret"])
    if not token:
        print("Failed to get tenant_access_token", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(pdf_path):
        pdf_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        if pdf_size_mb > 20:
            # PDF > 20MB: create TXT with arXiv URL instead
            arxiv_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            txt_path = pdf_path.replace('.pdf', '.txt')
            with open(txt_path, 'w') as f:
                f.write(arxiv_url)
            print(f"  PDF is {pdf_size_mb:.1f}MB > 20MB, uploading TXT with URL instead")
            file_token = upload_file(txt_path, token, config["app_token"])
            os.remove(txt_path)  # clean up temp txt
        else:
            file_token = upload_file(pdf_path, token, config["app_token"])
        if file_token:
            if "论文" not in fields:
                fields["论文"] = []
            fields["论文"].append({"file_token": file_token})

    fields = {k: v for k, v in fields.items() if v}

    # Guard: if title is empty, do not insert an empty record
    if not fields.get("题目"):
        print("ERROR: title field is empty, skipping Feishu insert", file=sys.stderr)
        sys.exit(1)

    # === Upsert: find existing record by title, update or insert ===
    title = fields.get("题目", "")
    existing_record_id, existing_fields = find_record_by_title(
        token, config["app_token"], config["table_id"], title
    )

    if existing_record_id:
        # Record exists → merge fields (new values override existing)
        merged = dict(existing_fields)
        merged.update(fields)
        # Preserve original PDF if new PDF is not provided
        if "论文" not in fields and existing_fields.get("论文"):
            merged["论文"] = existing_fields["论文"]
        print(f"Found existing record {existing_record_id} for '{title[:40]}...', updating {len(fields)} fields...")
        res = update_record(token, config["app_token"], config["table_id"], existing_record_id, merged)
        if res.get("code") == 0:
            print("Successfully updated existing record in Feishu!")
        else:
            print(f"Failed to update Feishu: {res}", file=sys.stderr)
            sys.exit(1)
    else:
        # No existing record → insert new
        print(f"No existing record for '{title[:40]}...', creating new record...")
        res = insert_record(token, config["app_token"], config["table_id"], fields)
        if res.get("code") == 0:
            print("Successfully synced new record to Feishu!")
        else:
            print(f"Failed to sync to Feishu: {res}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
