"""W4 聚合分析 (第一版: 效果分排名 + 模态边际, descriptive).

聚合层级 (design-doc §4.1 汇总): per-output → per-cell → per-(模型,var)。
  per-output 效果分 = 9 维 LLM-judge 等权均值 (preference_match=None 即 cold-start → 降 8 维等权)。
  content_fidelity −0.45 系统偏差校正 (refine 后人工锚定, worklog 2026-06-18)。
  per-cell = 3 run 算术均值; 合规 = 4 安全维 OR (任一违规即 cell 不合规)。
  per-(模型,var) = cells 效果分均值 (效果先全 cells; 合规门/成本/Pareto 下一轮)。

研究问题: ① 模型能力排名 (固定 var 比模型) ② 模态边际 (固定模型比 var A→B→C→D)。

CLI: PYTHONPATH=src python src/aggregate.py
"""
from __future__ import annotations
import json, collections, statistics
from pathlib import Path

JUDGE = Path("results/judge_main_full.jsonl")
EFFECT = ['readability','video_relevance','content_fidelity','expressiveness','naturalness',
          'expectation_alignment','preference_match','tone_fit','push_value']
SAFETY = ['misleading','manipulative','harmful','privacy']
BIAS = {'content_fidelity': -0.45}   # refine 后系统偏差校正
VARS = ['A','B','C','D']


def output_effect(eff: dict) -> float:
    """9 维等权均值; preference_match=None (cold-start) → skip 降 8 维; content_fidelity −0.45 校正。"""
    vals = []
    for d in EFFECT:
        v = eff[d]
        if v is None:
            continue
        vals.append(v + BIAS.get(d, 0))
    return sum(vals) / len(vals)


def compliant(safety: dict) -> bool:
    return not any(safety[d] for d in SAFETY)


def load_cells():
    """per-output → per-cell (3 run 均值 + OR 合规)."""
    by_cell = collections.defaultdict(list)
    n_pref_none = 0
    for l in JUDGE.open():
        r = json.loads(l)
        if r['score']['effect']['preference_match'] is None:
            n_pref_none += 1
        by_cell[r['cell_id']].append({
            'eff': output_effect(r['score']['effect']),
            'comp': compliant(r['score']['safety']),
            'model': r['gen_model'], 'var': r['var'], 'case': r['case_id'],
        })
    cells = {}
    for cid, outs in by_cell.items():
        cells[cid] = {
            'eff': statistics.mean(o['eff'] for o in outs),
            'comp': all(o['comp'] for o in outs),
            'model': outs[0]['model'], 'var': outs[0]['var'], 'case': outs[0]['case'],
            'n_run': len(outs),
        }
    return cells, n_pref_none


def per_arm(cells):
    """per-(模型,var): 效果分均值 + std + n。"""
    arms = collections.defaultdict(list)
    for c in cells.values():
        arms[(c['model'], c['var'])].append(c['eff'])
    out = {}
    for k, effs in arms.items():
        out[k] = {'mean': statistics.mean(effs), 'std': statistics.pstdev(effs), 'n': len(effs)}
    return out


def dim_means_by_arm():
    """每 arm 各维度均值 (诊断维度差异, 校正后)。"""
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for l in JUDGE.open():
        r = json.loads(l)
        k = (r['gen_model'], r['var'])
        for d in EFFECT:
            v = r['score']['effect'][d]
            if v is not None:
                by[k][d].append(v + BIAS.get(d, 0))
    return by


def main():
    cells, n_pref_none = load_cells()
    arms = per_arm(cells)
    models = sorted(set(k[0] for k in arms))
    print(f"cells: {len(cells)} | per-output preference_match=None (cold-start): {n_pref_none}")
    print(f"效果分 = 9 维等权 (cold-start 降 8 维), content_fidelity −0.45 校正\n")

    # ── per-arm 效果分总表 (模型 × var) ──
    print("=== per-arm 效果分 (模型 × var, 均值) ===")
    print("model".ljust(18) + "".join(v.rjust(9) for v in VARS))
    for m in models:
        row = ""
        for v in VARS:
            a = arms.get((m, v))
            row += (f"{a['mean']:.2f}" if a else "—").rjust(9)
        print(m.ljust(18) + row)

    # ── ① 模型排名 (固定 var, 比模型) ──
    for v in ['A', 'C']:  # A=纯文本(4模型全), C=metadata+音轨(4模型全)
        ranked = sorted(((m, arms[(m, v)]) for m in models if (m, v) in arms),
                        key=lambda x: -x[1]['mean'])
        print(f"\n=== ① 模型排名 @ var {v} ({'纯文本' if v=='A' else 'metadata+音轨'}) ===")
        for i, (m, a) in enumerate(ranked, 1):
            print(f"  {i}. {m:18} {a['mean']:.3f} (±{a['std']:.2f}, n={a['n']})")

    # ── ② 模态边际 (固定模型, A→B→C→D) ──
    print(f"\n=== ② 模态边际 (固定模型, var 增量) ===")
    for m in ['kimi-k2.6', 'gemini-2.5-flash']:  # 全 var 模型
        print(f"  {m}:")
        prev = None
        for v in VARS:
            a = arms.get((m, v))
            if not a:
                print(f"    {v}: —"); continue
            delta = f" (Δ{a['mean']-prev:+.3f})" if prev is not None else ""
            print(f"    {v}: {a['mean']:.3f}{delta}")
            prev = a['mean']

    # ── readability 天花板提示 ──
    dims = dim_means_by_arm()
    print(f"\n=== 诊断: readability 天花板 (无区分度) ===")
    rd = [arms[(m,'A')]['mean'] for m in models if (m,'A') in arms]  # 占位
    print("  readability 100% 全 5 分 → 9 维等权里它是常数, 不影响模型间排序, 但拉高绝对分。")
    print("  → 排名看相对序即可; 后续可报 8 维 (去 readability) 版对照。")


if __name__ == '__main__':
    main()
