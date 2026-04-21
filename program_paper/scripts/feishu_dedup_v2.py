#!/usr/bin/env python3
"""Find and remove duplicates in Feishu by Title (题目) field.
Uses urllib with 429 exponential backoff retry.
"""

import json
import urllib.request
import urllib.error
import time
from collections import defaultdict

APP_ID = "cli_a8cbc6155835500b"
APP_SECRET = "VIMYM708ZFDMQAglqNG09cMNLvqmmoi2"
APP_TOKEN = "BvrSbjhtdaPQ4ssda8hcEkvunxe"
TABLE_ID = "tblgAansubPtxhqA"


class UrlopenRetry:
    """urllib.urlopen wrapper with 429 exponential backoff retry."""
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
                    print(f"     [429] waiting {wait}s (attempt {attempt+1}/{self.max_retries})", file=__import__('sys').stderr)
                    time.sleep(wait)
                    continue
                raise
        return self.response

    def __exit__(self, *args):
        if self.response:
            self.response.close()


def api_get(url, token, params=None):
    """GET with 429 retry."""
    full_url = url
    if params:
        import urllib.parse
        full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"Authorization": f"Bearer {token}"})
    with UrlopenRetry(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def api_post(url, token, data_dict):
    """POST with 429 retry."""
    data = json.dumps(data_dict).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }, method="POST")
    with UrlopenRetry(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def api_delete(url, token):
    """DELETE with 429 retry."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    with UrlopenRetry(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def get_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with UrlopenRetry(req) as resp:
        return json.loads(resp.read().decode('utf-8')).get("tenant_access_token")


def get_all_records(token, page_size=100):
    records = []
    page_token = None
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        data = api_get(url, token, params)
        if data.get("code") != 0:
            print(f"Error fetching records: {data}")
            break
        items = data.get("data", {}).get("items", [])
        records.extend(items)
        print(f"  Fetched {len(items)} records (total so far: {len(records)})", file=__import__('sys').stderr)
        if not data.get("data", {}).get("has_more", False):
            break
        page_token = data.get("data", {}).get("page_token")
        time.sleep(0.5)  # Be nice to the API
    return records


def delete_record(token, record_id):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{record_id}"
    return api_delete(url, token)


def normalize(s):
    """Normalize title for comparison."""
    if not s:
        return ""
    return s.lower().strip()


def main():
    print("=" * 60)
    print("Feishu Deduplication V2 (with 429 retry)")
    print("=" * 60)

    token = get_token()
    if not token:
        print("Failed to get token")
        return

    print(f"Token obtained, fetching all records...")
    records = get_all_records(token)
    print(f"\nTotal records fetched: {len(records)}")

    # Group by normalized title
    by_title = defaultdict(list)
    for rec in records:
        fields = rec.get("fields", {})
        record_id = rec.get("record_id")
        last_modified = rec.get("last_modified_time", "")
        created_time = rec.get("created_time", "")
        title = fields.get("题目", "")

        if title:
            norm = normalize(title)
            if norm:
                by_title[norm].append({
                    "record_id": record_id,
                    "title": title[:80],
                    "last_modified": last_modified,
                    "created_time": created_time
                })

    print(f"Unique titles: {len(by_title)}")

    # Find duplicates
    duplicates = {k: v for k, v in by_title.items() if len(v) > 1}
    print(f"Titles with duplicates: {len(duplicates)}")

    total_deleted = 0
    for norm_title, recs in sorted(duplicates.items()):
        print(f"\n=== '{recs[0]['title'][:50]}...' ({len(recs)} records) ===")
        # Sort by last_modified, keep newest
        recs_sorted = sorted(recs, key=lambda x: x["last_modified"], reverse=True)
        keep = recs_sorted[0]
        delete = recs_sorted[1:]

        print(f"  KEEP: {keep['record_id']} | modified: {keep['last_modified']}")
        for r in delete:
            print(f"  DELETE: {r['record_id']}...", end=" ", flush=True)
            result = delete_record(token, r['record_id'])
            if result.get("code") == 0:
                total_deleted += 1
                print("→ Deleted")
            else:
                print(f"→ Failed: {result}")
            time.sleep(0.3)  # Be nice to the API

    print(f"\n{'=' * 60}")
    print(f"DEDUP COMPLETE")
    print(f"  Total records : {len(records)}")
    print(f"  Unique titles : {len(by_title)}")
    print(f"  Duplicate groups: {len(duplicates)}")
    print(f"  Records deleted: {total_deleted}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
