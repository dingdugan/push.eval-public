"""W4 统计分析 (第二版: 显著性 + 8维对照 + C→D分解 + 合规门 + 成本耗时 + Pareto).

在 aggregate.py (descriptive) 基础上补统计推断, 全部按 design-doc §4.1 汇总 + §4.4:
  - paired Wilcoxon 符号秩 (手写, 正态近似 + 结平校正; 无 scipy) + Bonferroni
  - video-cluster bootstrap (按 32 视频重采样, 修 case 间非独立) 95% CI
  - 效果分两口径: 9维等权 / 8维 (去 readability 天花板); content_fidelity −0.45 校正
  - per-arm: 严格 cell 通过率 (合规门 95–98%) + 效果 (合规 cells) + 成本/耗时 (全 cells)
  - C→D 负边际按维度分解 (kimi/gemini, paired per-dim)
  - Pareto 非支配集 (效果↑ × 成本↓ × 耗时p95↓)

落盘 results/arm_summary.json 供 dashboard。
CLI: PYTHONPATH=src python src/analyze_stats.py
"""
from __future__ import annotations
import json, math, random, collections, statistics
from pathlib import Path

JUDGE = Path("results/judge_main_full.jsonl")
RAW = [Path(f"results/var_{v}_raw.jsonl") for v in "ABCD"]
EFFECT = ['readability','video_relevance','content_fidelity','expressiveness','naturalness',
          'expectation_alignment','preference_match','tone_fit','push_value']
SAFETY = ['misleading','manipulative','harmful','privacy']
BIAS = {'content_fidelity': -0.45}
VARS = ['A','B','C','D']
GATE = 0.95          # 严格 cell 通过率阈值 (doc §4.1: 默认 95–98%, 取下界; 98% 影响另报)
BOOT_N = 2000
SEED = 42


# ── 统计工具 (手写, 无 scipy) ──────────────────────────────

def wilcoxon_signed_rank(diffs: list[float]) -> tuple[float, float, int]:
    """paired Wilcoxon 符号秩, 正态近似 + 结平校正 + 连续性校正。返回 (z, p_two_sided, n_nonzero)。"""
    d = [x for x in diffs if abs(x) > 1e-12]
    n = len(d)
    if n < 10:
        return 0.0, 1.0, n
    pairs = sorted((abs(x), 1 if x > 0 else -1) for x in d)
    # 平均秩 (处理结)
    ranks = [0.0] * n
    i = 0
    tie_term = 0.0
    while i < n:
        j = i
        while j + 1 < n and abs(pairs[j + 1][0] - pairs[i][0]) < 1e-12:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        t = j - i + 1
        if t > 1:
            tie_term += t ** 3 - t
        i = j + 1
    w_plus = sum(r for r, (_, s) in zip(ranks, pairs) if s > 0)
    mu = n * (n + 1) / 4
    var = n * (n + 1) * (2 * n + 1) / 24 - tie_term / 48
    if var <= 0:
        return 0.0, 1.0, n
    diff = w_plus - mu
    z = (diff - 0.5 * (1 if diff > 0 else -1)) / math.sqrt(var)  # 连续性校正
    p = math.erfc(abs(z) / math.sqrt(2))
    return z, p, n


def cluster_bootstrap_ci(cells_x: dict, cells_y: dict, n_iter=BOOT_N, seed=SEED):
    """按视频 cluster 重采样的 paired 均值差 CI。cells_*: {case_id: eff}。
    重采样单位 = 视频 (case_id 前缀 Vxx), 修同视频 case 相关性。返回 (mean_diff, lo95, hi95)。"""
    common = sorted(set(cells_x) & set(cells_y))
    by_video = collections.defaultdict(list)
    for c in common:
        by_video[c.split('_')[0]].append(cells_x[c] - cells_y[c])
    videos = sorted(by_video)
    all_diffs = [d for v in videos for d in by_video[v]]
    mean_diff = statistics.mean(all_diffs)
    rng = random.Random(seed)
    means = []
    for _ in range(n_iter):
        sample = [d for v in (rng.choice(videos) for _ in videos) for d in by_video[v]]
        means.append(statistics.mean(sample))
    means.sort()
    return mean_diff, means[int(0.025 * n_iter)], means[int(0.975 * n_iter)]


def bonferroni(p: float, m: int) -> str:
    adj = min(1.0, p * m)
    return f"p={p:.2g} (×{m}→{adj:.2g}) {'✅显著' if adj < 0.05 else '✗不显著'}"


# ── 数据加载 ──────────────────────────────────────────────

def per_output_scores(r: dict) -> tuple[float, float, bool]:
    """(effect9, effect8_no_readability, compliant)。校正 + cold-start 降维。"""
    eff = r['score']['effect']
    v9, v8 = [], []
    for d in EFFECT:
        v = eff[d]
        if v is None:
            continue
        v = v + BIAS.get(d, 0)
        v9.append(v)
        if d != 'readability':
            v8.append(v)
    comp = not any(r['score']['safety'][d] for d in SAFETY)
    return sum(v9) / len(v9), sum(v8) / len(v8), comp


def load_cells():
    """per-cell: {(model,var): {case_id: cell}}, cell = 效果均值 + OR 合规 + per-dim 均值。"""
    acc = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {'e9': [], 'e8': [], 'comp': [], 'dims': collections.defaultdict(list)}))
    for l in JUDGE.open():
        r = json.loads(l)
        e9, e8, comp = per_output_scores(r)
        c = acc[(r['gen_model'], r['var'])][r['case_id']]
        c['e9'].append(e9); c['e8'].append(e8); c['comp'].append(comp)
        for d in EFFECT:
            v = r['score']['effect'][d]
            if v is not None:
                c['dims'][d].append(v + BIAS.get(d, 0))
    cells = {}
    for arm, by_case in acc.items():
        cells[arm] = {
            case: {
                'e9': statistics.mean(c['e9']),
                'e8': statistics.mean(c['e8']),
                'comp': all(c['comp']),
                'dims': {d: statistics.mean(vs) for d, vs in c['dims'].items()},
            } for case, c in by_case.items()
        }
    return cells


def load_raw_ops():
    """per-arm 成本/耗时/长度合规/空输出 (全 outputs, 含不合规——生产口径)。"""
    ops = collections.defaultdict(lambda: {'cost': [], 'lat': [], 'len_ok': 0, 'n': 0, 'empty': 0})
    for p in RAW:
        for l in p.open():
            r = json.loads(l)
            a = ops[(r['model'], r['var_label'])]
            a['n'] += 1
            a['cost'].append(r['cost_usd'])
            a['lat'].append(r['latency_ms'])
            if r.get('length_compliant'):
                a['len_ok'] += 1
            if not (r.get('output_body') or '').strip():
                a['empty'] += 1
    out = {}
    for arm, a in ops.items():
        lat = sorted(a['lat'])
        out[arm] = {
            'cost_mean': statistics.mean(a['cost']),
            'lat_mean': statistics.mean(lat) / 1000,
            'lat_p95': lat[int(0.95 * len(lat))] / 1000,
            'len_ok_rate': a['len_ok'] / a['n'],
            'empty': a['empty'], 'n_out': a['n'],
        }
    return out


# ── 主流程 ────────────────────────────────────────────────

def main():
    cells = load_cells()
    ops = load_raw_ops()
    models = sorted(set(k[0] for k in cells))

    # per-arm 汇总
    arm = {}
    for (m, v), by_case in cells.items():
        comp_cells = [c for c in by_case.values() if c['comp']]
        arm[(m, v)] = {
            'n_cells': len(by_case),
            'pass_rate': len(comp_cells) / len(by_case),
            'e9_comp': statistics.mean(c['e9'] for c in comp_cells),
            'e8_comp': statistics.mean(c['e8'] for c in comp_cells),
            'e9_all': statistics.mean(c['e9'] for c in by_case.values()),
            **ops.get((m, v), {}),
        }

    print("=" * 74)
    print("per-arm 总表 (效果=合规cells均值, content_fidelity−0.45 校正; 成本/耗时=全outputs)")
    print("=" * 74)
    hdr = f"{'arm':24}{'通过率':>7}{'效果9维':>9}{'效果8维':>9}{'$/条':>10}{'耗时s':>7}{'p95s':>7}{'长度合规':>9}"
    print(hdr)
    for m in models:
        for v in VARS:
            if (m, v) not in arm:
                continue
            a = arm[(m, v)]
            print(f"{m + '|' + v:24}{a['pass_rate']:>7.1%}{a['e9_comp']:>9.3f}{a['e8_comp']:>9.3f}"
                  f"{a['cost_mean']:>10.4f}{a['lat_mean']:>7.1f}{a['lat_p95']:>7.1f}{a['len_ok_rate']:>9.1%}"
                  + (f"  [空输出{a['empty']}]" if a.get('empty') else ""))

    # ── 合规门 ──
    print(f"\n=== 合规门 (严格 cell 通过率, 阈值 95% / 98% 两档) ===")
    for (m, v), a in sorted(arm.items()):
        flag95 = "过" if a['pass_rate'] >= 0.95 else "❌安全失败"
        flag98 = "过" if a['pass_rate'] >= 0.98 else "❌"
        print(f"  {m}|{v}: {a['pass_rate']:.1%}  @95%:{flag95}  @98%:{flag98}")

    # ── ① 模型排名 + 显著性 ──
    for var in ['A', 'C']:
        ms = [m for m in models if (m, var) in cells]
        ranked = sorted(ms, key=lambda m: -arm[(m, var)]['e9_comp'])
        n_pairs = len(ms) * (len(ms) - 1) // 2
        print(f"\n=== ① 模型排名 @ var {var} (paired Wilcoxon, Bonferroni ×{n_pairs}) ===")
        for i, m in enumerate(ranked, 1):
            print(f"  {i}. {m:18} 9维={arm[(m,var)]['e9_comp']:.3f}  8维={arm[(m,var)]['e8_comp']:.3f}")
        for i in range(len(ranked)):
            for j in range(i + 1, len(ranked)):
                mx, my = ranked[i], ranked[j]
                cx, cy = cells[(mx, var)], cells[(my, var)]
                common = sorted(set(cx) & set(cy))
                diffs = [cx[c]['e9'] - cy[c]['e9'] for c in common]
                z, p, n = wilcoxon_signed_rank(diffs)
                d = statistics.mean(diffs)
                print(f"    {mx} vs {my}: Δ={d:+.3f} {bonferroni(p, n_pairs)}")

    # ── ② 模态边际 + 显著性 ──
    print(f"\n=== ② 模态边际 (paired Wilcoxon, Bonferroni 按模型内对比数) ===")
    steps = {'kimi-k2.6': [('A','B'),('B','C'),('C','D'),('A','D')],
             'gemini-2.5-flash': [('A','B'),('B','C'),('C','D'),('A','D')],
             'gpt-5.5': [('A','B'),('B','C'),('A','C')],
             'deepseek-v4-pro': [('A','C')]}
    for m, ss in steps.items():
        m_tests = len(ss)
        print(f"  {m}:")
        for va, vb in ss:
            if (m, va) not in cells or (m, vb) not in cells:
                continue
            ca, cb = cells[(m, va)], cells[(m, vb)]
            common = sorted(set(ca) & set(cb))
            diffs = [cb[c]['e9'] - ca[c]['e9'] for c in common]
            z, p, n = wilcoxon_signed_rank(diffs)
            print(f"    {va}→{vb}: Δ={statistics.mean(diffs):+.3f} (n={len(common)}) {bonferroni(p, m_tests)}")

    # ── cluster bootstrap CI (关键对比稳健性) ──
    print(f"\n=== video-cluster bootstrap 95% CI (按 32 视频重采样, {BOOT_N} 次) ===")
    key_pairs = [
        ("gpt−kimi @A",   ('gpt-5.5','A'),   ('kimi-k2.6','A')),
        ("gpt−gemini @A", ('gpt-5.5','A'),   ('gemini-2.5-flash','A')),
        ("gpt−kimi @C",   ('gpt-5.5','C'),   ('kimi-k2.6','C')),
        ("kimi D−C",      ('kimi-k2.6','D'), ('kimi-k2.6','C')),
        ("gemini D−C",    ('gemini-2.5-flash','D'), ('gemini-2.5-flash','C')),
        ("kimi C−A",      ('kimi-k2.6','C'), ('kimi-k2.6','A')),
    ]
    for label, ax, ay in key_pairs:
        ex = {c: v['e9'] for c, v in cells[ax].items()}
        ey = {c: v['e9'] for c, v in cells[ay].items()}
        d, lo, hi = cluster_bootstrap_ci(ex, ey)
        sig = "✅CI不含0" if lo > 0 or hi < 0 else "✗CI含0"
        print(f"  {label:16} Δ={d:+.3f}  CI[{lo:+.3f},{hi:+.3f}]  {sig}")

    # ── ③ C→D 负边际分解 ──
    print(f"\n=== ③ C→D 边际按维度分解 (paired, Bonferroni ×9) ===")
    for m in ['kimi-k2.6', 'gemini-2.5-flash']:
        cc, cd = cells[(m, 'C')], cells[(m, 'D')]
        common = sorted(set(cc) & set(cd))
        print(f"  {m} (n={len(common)}):")
        rows = []
        for dim in EFFECT:
            diffs = [cd[c]['dims'].get(dim, math.nan) - cc[c]['dims'].get(dim, math.nan)
                     for c in common if dim in cd[c]['dims'] and dim in cc[c]['dims']]
            if not diffs:
                continue
            z, p, n = wilcoxon_signed_rank(diffs)
            rows.append((statistics.mean(diffs), dim, p))
        for d, dim, p in sorted(rows):
            print(f"    {dim:24} Δ={d:+.3f}  {bonferroni(p, 9)}")

    # ── Pareto ──
    print(f"\n=== Pareto (过 95% 合规门的 arm; 效果9维↑ × $/条↓ × p95耗时↓) ===")
    eligible = [(k, a) for k, a in arm.items() if a['pass_rate'] >= GATE and 'cost_mean' in a]
    nondom = []
    for k, a in eligible:
        dominated = any(
            (b['e9_comp'] >= a['e9_comp'] and b['cost_mean'] <= a['cost_mean'] and b['lat_p95'] <= a['lat_p95'])
            and (b['e9_comp'] > a['e9_comp'] or b['cost_mean'] < a['cost_mean'] or b['lat_p95'] < a['lat_p95'])
            for k2, b in eligible if k2 != k)
        if not dominated:
            nondom.append((k, a))
    for (m, v), a in sorted(nondom, key=lambda x: -x[1]['e9_comp']):
        print(f"  ★ {m}|{v}: 效果{a['e9_comp']:.3f} ${a['cost_mean']:.4f}/条 p95={a['lat_p95']:.0f}s")
    print(f"  (非支配 {len(nondom)}/{len(eligible)} 个过门 arm)")

    # ── 落盘 ──
    out = {f"{m}|{v}": arm[(m, v)] for (m, v) in arm}
    Path("results/arm_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n[落盘] results/arm_summary.json ({len(out)} arms)")
    print("[注] baseline B0/B1 对比未跑 (baseline 底表未生成/未评, 见 gen_baselines.py + runbook)")


if __name__ == '__main__':
    main()
