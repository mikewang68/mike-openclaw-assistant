import sys
sys.path.insert(0, '/program/knowledge')
from entity_discovery import discovery_for_paper, append_judgments
import glob, os

doc_ids = [os.path.basename(f).replace('.json', '') for f in sorted(glob.glob('/tmp/triples_2026-04-17-*.json'))]
print(f'Running discovery for {len(doc_ids)} papers')

for doc_id in doc_ids:
    md_path = f'/obsidian/01_Input/05_PDF2MD/{doc_id}.md'
    if not os.path.exists(md_path):
        print(f'  SKIP {doc_id}: no MD file')
        continue
    try:
        result = discovery_for_paper(doc_id, md_path)
        print(f'  Discovery {doc_id}: {result["total_candidates"]} candidates')
    except Exception as e:
        print(f'  ERROR {doc_id}: {e}')
