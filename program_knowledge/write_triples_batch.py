import json, glob, os
import sys
sys.path.insert(0, '/program/knowledge')
from mongo_writer import MongoWriter

os.chdir('/program/knowledge')

writer = MongoWriter()

triples_files = sorted(glob.glob('/tmp/triples_2026-04-17-*.json'))
print(f'Found {len(triples_files)} triples files')

total = 0
for fpath in triples_files:
    doc_id = os.path.basename(fpath).replace('.json', '')
    try:
        with open(fpath) as f:
            data = json.load(f)
        triples = data.get('triples', data) if isinstance(data, dict) else data
        if isinstance(triples, dict) and 'triples' in triples:
            triples = triples['triples']
        count = writer.write_triples(triples, source=doc_id)
        total += count
        print(f'  Wrote {count} triples for {doc_id}')
    except Exception as e:
        print(f'  ERROR {doc_id}: {e}')

print(f'Total: {total} triples written')
writer.close()
