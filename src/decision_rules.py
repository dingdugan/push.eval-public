"""§5 决策规则执行 — 把 §4 统计结果按 doc §5 三层规则跑成上线推荐。

三层 (doc §5.1/5.2/5.3), 阈值全部取自 doc 明文, 不另行发明:
  5.1 方向判断: 存在 arm 同时 ① 过合规门 ② vs B0 显著 ③ 真实分差 ≥ 0.3 (power 边界)
  5.2 候选筛选: 3 个硬 filter 同时满足 — 合规(严格 cell ≥95% + 长度合规 ≥90%) / vs B0(显著且 ≥0.3) / vs B1(≥0.2)
  5.3 推荐选型: 候选池内 Pareto front 上「效果差 < 0.2 (最小可检出增量)」的点里取成本最低
  5.4 输出: 生成给业务侧的推荐结论

效果分口径同 §4.1 汇总 (9 维等权 + content_fidelity −0.45 校正 + 合规 cells)。
CLI: PYTHONPATH=src python src/decision_rules.py
"""
from __future__ import annotations
import json, statistics
from pathlib import Path

from analyze_stats import load_cells, load_raw_ops, wilcoxon_signed_rank, GATE
from gen_dashboard_data import build_baseline, load_case_meta

LEN_GATE = 0.90      # §5.2 长度合规 pass 率
MIN_DETECT = 0.3     # §5.1 真实分差 (power 边界)
B1_MIN = 0.2         # §5.2 vs B1 最小可检出增量
NEAR_OPT = 0.2       # §5.3 「接近最优」带宽


def main():
    cells = load_cells()
    ops = load_raw_ops()
    bl = build_baseline(cells, load_case_meta())
    cmp_idx = {(c['arm'], c['baseline']): c for c in bl['comparisons']}

    arms = {}
    for (m, v), by_case in cells.items():
        comp = [c for c in by_case.values() if c['comp']]
        o = ops.get((m, v), {})
        key = f"{m}|{v}"
        arms[key] = {
            'model': m, 'var': v,
            'pass_rate': len(comp) / len(by_case),
            'e9': statistics.mean(c['e9'] for c in comp),
            'len_ok': o.get('len_ok_rate', 0),
            'cost': o.get('cost_mean', 0),
            'lat_mean': o.get('lat_mean', 0), 'lat_p95': o.get('lat_p95', 0),
            'vs_b0': cmp_idx[(key, 'B0')], 'vs_b1': cmp_idx[(key, 'B1')],
        }

    print("=" * 78)
    print("§5.1 方向判断 (推进 / 不推进)")
    print("=" * 78)
    qualified = [k for k, a in arms.items()
                 if a['pass_rate'] >= GATE and a['vs_b0']['sig'] and a['vs_b0']['delta'] >= MIN_DETECT]
    print(f"  条件: 合规门 ≥{GATE:.0%} AND vs B0 显著 AND vs B0 Δ ≥ {MIN_DETECT}")
    print(f"  满足的 arm: {len(qualified)}/{len(arms)}")
    print(f"  判定: {'✅ 推进 → 进入 5.2' if qualified else '❌ 不推进'}")

    print()
    print("=" * 78)
    print("§5.2 候选筛选 (3 个硬 filter 同时满足)")
    print("=" * 78)
    hdr = f"{'arm':22}{'严格cell':>9}{'长度合规':>9}{'vsB0':>8}{'vsB1':>8}  判定"
    print(hdr); print("-" * 78)
    pool = []
    for k, a in sorted(arms.items(), key=lambda x: -x[1]['e9']):
        f1 = a['pass_rate'] >= GATE and a['len_ok'] >= LEN_GATE
        f2 = a['vs_b0']['sig'] and a['vs_b0']['delta'] >= MIN_DETECT
        f3 = a['vs_b1']['delta'] >= B1_MIN
        ok = f1 and f2 and f3
        if ok:
            pool.append(k)
        why = "✅ 进候选池" if ok else "❌ " + ", ".join(
            x for x, c in [(f"长度合规{a['len_ok']:.1%}<90%", not (a['len_ok'] >= LEN_GATE)),
                           (f"严格cell{a['pass_rate']:.1%}<95%", not (a['pass_rate'] >= GATE)),
                           ("vsB0不足", not f2), ("vsB1不足", not f3)] if c)
        print(f"{k:22}{a['pass_rate']:>9.1%}{a['len_ok']:>9.1%}"
              f"{a['vs_b0']['delta']:>+8.2f}{a['vs_b1']['delta']:>+8.2f}  {why}")
    print(f"\n  候选池 ({len(pool)}): {pool if pool else '空'}")

    print()
    print("=" * 78)
    print("§5.3 推荐选型")
    print("=" * 78)
    if not pool:
        print("  候选池为空 → 无推荐"); return
    best = max(pool, key=lambda k: arms[k]['e9'])
    near = [k for k in pool if arms[best]['e9'] - arms[k]['e9'] < NEAR_OPT]
    primary = min(near, key=lambda k: arms[k]['cost'])
    print(f"  效果最高者: {best} ({arms[best]['e9']:.3f})")
    print(f"  「效果差 < {NEAR_OPT}」的点: {near}")
    print(f"  其中成本最低 → Primary candidate: ★ {primary}")
    a = arms[primary]
    print(f"\n  ★ {primary}: 效果 {a['e9']:.3f} | vs B0 {a['vs_b0']['delta']:+.2f} | vs B1 {a['vs_b1']['delta']:+.2f}"
          f" | ${a['cost']:.4f}/条 | 安全 {a['pass_rate']:.1%} | 长度合规 {a['len_ok']:.1%}"
          f" | 耗时 {a['lat_mean']:.1f}s (p95 {a['lat_p95']:.1f}s)")
    # premium / budget
    prem = [k for k in pool if arms[k]['e9'] - a['e9'] >= NEAR_OPT]
    print(f"  Premium candidate (效果显著更高 ≥{NEAR_OPT}): {prem if prem else '无 — 候选池内效果差距全部 < 0.2'}")
    cheaper = [k for k in pool if arms[k]['cost'] < a['cost']]
    print(f"  Budget candidate (更便宜): {cheaper if cheaper else '无 — Primary 已是候选池成本最低'}")

    out = {'qualified_5_1': qualified, 'pool_5_2': pool, 'primary_5_3': primary,
           'arms': {k: {kk: vv for kk, vv in v.items() if kk not in ('vs_b0', 'vs_b1')} |
                    {'vs_b0_delta': v['vs_b0']['delta'], 'vs_b0_sig': v['vs_b0']['sig'],
                     'vs_b1_delta': v['vs_b1']['delta'], 'vs_b1_sig': v['vs_b1']['sig']}
                    for k, v in arms.items()},
           'thresholds': {'gate': GATE, 'len_gate': LEN_GATE, 'min_detect': MIN_DETECT,
                          'b1_min': B1_MIN, 'near_opt': NEAR_OPT}}
    Path('results/decision.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n[落盘] results/decision.json")


if __name__ == '__main__':
    main()
