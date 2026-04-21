import sys
sys.path.insert(0, '/program/knowledge')
from pipeline import discovery_for_paper
import glob, os, json

doc_ids = [os.path.basename(f).replace('.json', '').replace('triples_', '') for f in sorted(glob.glob('/tmp/triples_2026-04-17-*.json'))]
print(f'Building discovery prompts for {len(doc_ids)} papers')

prompts = {}
for doc_id in doc_ids:
    md_path = f'/obsidian/01_Input/05_PDF2MD/{doc_id}.md'
    if not os.path.exists(md_path):
        print(f'  SKIP {doc_id}: no MD file')
        continue
    try:
        result = discovery_for_paper(doc_id, md_path)
        prompts[doc_id] = result
        print(f'  {doc_id}: {result["candidates"]} candidates')
    except Exception as e:
        print(f'  ERROR {doc_id}: {e}')

print(f'\nTotal: {len(prompts)} prompts built')
with open('/tmp/discovery_prompts.json', 'w') as f:
    json.dump({k: {'candidates': v['candidates'], 'prompt': v['prompt']} for k, v in prompts.items()}, f, ensure_ascii=False, indent=2)
print('Saved to /tmp/discovery_prompts.json')
