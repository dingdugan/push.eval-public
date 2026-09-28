"""全量主评分批 QA — 每跑 ~10% 停下来检查这批 judge 落盘有没有错漏。

检查: 行数/重复 / score 回灌(13维合法) / whys 完整性(13维理由齐+非空) /
      effect 各维分布(天花板/异常) / safety 违规率 / cost 对账+外推 / 异常值。
红灯(parse 暴跌 / whys 缺 / 分数异常)→ 停下排查, 不继续烧钱。

CLI: PYTHONPATH=src python src/judge_qa.py <judge落盘.jsonl> [--expect-total 16224]
"""
from __future__ import annotations
import argparse, json, collections
from pathlib import Path
from schemas import LLMJudgeScore

EFFECT = ['readability','video_relevance','content_fidelity','expressiveness','naturalness',
          'expectation_alignment','preference_match','tone_fit','push_value']
SAFETY = ['misleading','manipulative','harmful','privacy']


def qa(path: str, expect_total: int = 16224):
    p = Path(path)
    if not p.exists():
        print(f"⚠️ {path} 不存在"); return
    rows = [json.loads(l) for l in p.open(encoding='utf-8') if l.strip()]
    n = len(rows)
    flags = []
    print(f"\n{'='*64}\n{path} — {n} 行 ({n/expect_total*100:.0f}% of {expect_total})")

    # 1. 重复
    keys = [(r['cell_id'], r['run_id'], r['judge_model']) for r in rows]
    dup = n - len(set(keys))
    print(f"  重复 (cell,run,judge): {dup}" + (" 🔴" if dup else " ✅"))
    if dup: flags.append("有重复")

    # 2. score 回灌 (13维合法)
    sfail = []
    for i, r in enumerate(rows):
        try: LLMJudgeScore(**{'judge_model': r['judge_model'], **r['score']})
        except Exception as e:
            if len(sfail) < 3: sfail.append(f"行{i}:{str(e)[:50]}")
    print(f"  score 回灌: {'✅ 全合法' if not sfail else '🔴 '+str(sfail)}")
    if sfail: flags.append("score 非法")

    # 3. whys 完整性
    why_miss = why_empty = 0
    for r in rows:
        w = r.get('whys', {}); we = w.get('effect', {}); ws = w.get('safety', {})
        if len(we) < 9 or len(ws) < 4: why_miss += 1
        why_empty += sum(1 for v in list(we.values())+list(ws.values()) if not (v or '').strip())
    print(f"  whys: 缺维度的行 {why_miss} | 空 why {why_empty}/{n*13} ({why_empty/(n*13)*100:.1f}%)"
          + (" 🔴" if why_miss else " ✅"))
    if why_miss: flags.append("whys 缺维度")

    # 4. effect 分布 (天花板/异常)
    print("  effect 分布 (众数占比, >85%=天花板):")
    for d in EFFECT:
        c = collections.Counter(r['score']['effect'][d] for r in rows if r['score']['effect'][d] is not None)
        tot = sum(c.values())
        if not tot: print(f"    {d:22} 全 None"); continue
        v, cnt = c.most_common(1)[0]; pct = cnt/tot*100
        flag = " 🔴天花板" if pct > 85 else ""
        print(f"    {d:22} {dict(sorted(c.items()))} 众数{v}={pct:.0f}%{flag}")

    # 5. safety 违规率
    print("  safety 违规率:")
    for d in SAFETY:
        v = sum(1 for r in rows if r['score']['safety'][d])
        print(f"    {d:14} {v}/{n} ({v/n*100:.1f}%)")

    # 6. cost 对账 + 外推
    cost = sum(r.get('cost_usd', 0) for r in rows)
    print(f"  cost: ${cost:.2f} 累计 | ${cost/n:.4f}/条 | 外推全量 ${cost/n*expect_total:.0f}")

    # 7. 单条耗时
    lat = [r.get('latency_ms', 0)/1000 for r in rows if r.get('latency_ms')]
    if lat: print(f"  单条耗时: 均 {sum(lat)/len(lat):.0f}s | max {max(lat):.0f}s")

    print(f"\n  {'🟢 这批通过, 可继续下一批' if not flags else '🔴 停! 问题: '+', '.join(flags)}")
    return flags


if __name__ == '__main__':
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--expect-total', type=int, default=16224)
    a = ap.parse_args()
    flags = qa(a.path, a.expect_total)
    sys.exit(1 if flags else 0)   # 红灯(硬错漏) exit 1, wrapper 据此停
