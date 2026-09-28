#!/bin/bash
# 查全量主评进度: bash ops/progress.sh
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
PYTHONPATH=src .venv/bin/python -c "
import json, subprocess
from datetime import datetime, timezone
rows=[json.loads(l) for l in open('results/judge_main_full.jsonl')]
n=len(rows); T=16185
ts=sorted(datetime.fromisoformat(r['judged_at']) for r in rows if r.get('judged_at'))
rec=ts[-100:] if len(ts)>=100 else ts
sp=(rec[-1]-rec[0]).total_seconds() or 1
rpm=len(rec)/sp*60
ago=(datetime.now(timezone.utc)-ts[-1]).total_seconds()/60
run=len(subprocess.run(['pgrep','-f','judge_runner.py --mode main'],capture_output=True).stdout.split())
st='✅在涨' if ago<3 else ('🔴停了/卡了' if ago>8 else '⚠️变慢')
eta=(T-n)/rpm/60 if rpm>0 else 0
print(f'{n}/{T} ({n/T*100:.1f}%) | {rpm:.1f}条/分 {st}(最后落盘{ago:.0f}分前) | runner {run}进程 | 剩{T-n}条 ETA{eta:.1f}h')
" 2>/dev/null
