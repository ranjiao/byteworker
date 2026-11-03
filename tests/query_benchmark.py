"""Run: python3 tests/query_benchmark.py [--baseline-ref REF]. Synthetic recall only."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import types
from test_review_query_baseline import records
import kb_query

parser = argparse.ArgumentParser()
parser.add_argument('--baseline-ref')
args = parser.parse_args()
engine = kb_query
if args.baseline_ref:
    source = subprocess.run(['git', 'show', args.baseline_ref + ':lib/kb_query.py'], check=True, capture_output=True, text=True).stdout
    engine = types.ModuleType('baseline_query')
    exec(compile(source, 'baseline_kb_query.py', 'exec'), engine.__dict__)
data = records()
queries = [(q, key) for key, row in data.items() for q in
           (row['title'], '请问'+row['title']+'最近怎么样', '我想了解'+row['title']+'的情况', key)]
start = time.perf_counter()
hits = 0
for query, expected in queries:
    candidates = engine._search_records(data, query, graph_depth=0)['candidates']
    hits += bool(candidates and candidates[0]['id'] == expected)
print(json.dumps(dict(queries=len(queries), top1=hits, elapsed_ms=round((time.perf_counter()-start)*1000, 3)), ensure_ascii=False))
