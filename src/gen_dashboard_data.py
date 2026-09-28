"""L1 结果 Dashboard 数据生成 → web/data_results.js

从 judge_main_full.jsonl (+ var_*_raw.jsonl + test_cases.jsonl) 聚合出 dashboard 全部数据:
  arms 总表 / 排名+显著性 / 模态阶梯 / C→D 分解 / bootstrap CI / Pareto / 下钻样例(含 whys)。
baseline: results/judge_baseline.jsonl 存在则自动算 LLM-vs-B0/B1 对比, 否则置 null (前端显示待跑)。
复用 analyze_stats 的统计函数 (同一套口径, 不重写)。

CLI: PYTHONPATH=src python src/gen_dashboard_data.py   (数据更新后重跑即可刷新 dashboard)
"""
from __future__ import annotations
import json, statistics
from datetime import date
from pathlib import Path

from analyze_stats import (EFFECT, SAFETY, BIAS, VARS, GATE,
                           wilcoxon_signed_rank, cluster_bootstrap_ci,
                           per_output_scores, load_cells, load_raw_ops)

JUDGE = Path("results/judge_main_full.jsonl")
BASELINE_JUDGE = Path("results/judge_baseline.jsonl")
DECISION = Path("results/decision.json")   # §5 决策规则判定 (src/decision_rules.py 产出)
CASES = Path("data/test_cases.jsonl")
OUT = Path("web/data_results.js")

MODEL_STEPS = {'kimi-k2.6': ['A', 'B', 'C', 'D'],
               'gemini-2.5-flash': ['A', 'B', 'C', 'D'],
               'gpt-5.5': ['A', 'B', 'C'],
               'deepseek-v4-pro': ['A', 'C']}


def load_case_meta():
    meta = {}
    for l in CASES.open(encoding='utf-8'):
        c = json.loads(l)
        p = c['persona']
        cats = (p.get('content_preference') or {}).get('categories') or []
        meta[c['case_id']] = {
            'video_title': c['video']['video_title'],
            'youtube_id': c['video']['youtube_id'],
            'persona': f"{p['persona_id']} · {p['lifecycle']}" + (f" · 偏好 {'/'.join(cats[:2])}" if cats else " · 无偏好(cold-start)"),
            'persona_en': f"{p['persona_id']} · {p['lifecycle']}" + (f" · prefers {'/'.join(cats[:2])}" if cats else " · no preference (cold-start)"),
        }
    return meta


def pairwise(cells, var, models, n_tests):
    ranked = sorted((m for m in models if (m, var) in cells),
                    key=lambda m: -statistics.mean(c['e9'] for c in cells[(m, var)].values() if c['comp']))
    pairs = []
    for i in range(len(ranked)):
        for j in range(i + 1, len(ranked)):
            mx, my = ranked[i], ranked[j]
            cx, cy = cells[(mx, var)], cells[(my, var)]
            common = sorted(set(cx) & set(cy))
            diffs = [cx[c]['e9'] - cy[c]['e9'] for c in common]
            z, p, n = wilcoxon_signed_rank(diffs)
            p_adj = min(1.0, p * n_tests)
            pairs.append({'a': mx, 'b': my, 'delta': round(statistics.mean(diffs), 4),
                          'p_adj': float(f"{p_adj:.3g}"), 'sig': p_adj < 0.05})
    return ranked, pairs


def build_examples(cells, case_meta):
    """每 arm 最高/最低效果 cell 各 1, 附代表 run 的 output + 13 维原始分 + whys。"""
    # 选 cell
    want = {}   # cell_id → (arm_key, kind, pick)  pick: 'max'|'min'
    for (m, v), by_case in cells.items():
        comp = {c: d for c, d in by_case.items() if d['comp']} or by_case
        best = max(comp, key=lambda c: comp[c]['e9'])
        worst = min(comp, key=lambda c: comp[c]['e9'])
        want[f"{m}|{v}|{best}"] = (f"{m}|{v}", 'best')
        want[f"{m}|{v}|{worst}"] = (f"{m}|{v}", 'worst')
    # 扫 judge 文件收这些 cell 的 rows
    rows_by_cell = {}
    for l in JUDGE.open():
        r = json.loads(l)
        cid = r['cell_id']
        if cid in want:
            rows_by_cell.setdefault(cid, []).append(r)
    # 原文 join
    text = {}
    for p in [Path(f"results/var_{v}_raw.jsonl") for v in "ABCD"]:
        for l in p.open():
            r = json.loads(l)
            text[(r['cell_id'], r['run_id'])] = (r['output_title'], r['output_body'])
    ex = {}
    for cid, (arm, kind) in want.items():
        rows = rows_by_cell.get(cid)
        if not rows:
            continue
        pick = max if kind == 'best' else min
        r = pick(rows, key=lambda x: per_output_scores(x)[0])
        e9 = per_output_scores(r)[0]
        t, b = text.get((r['cell_id'], r['run_id']), ('', ''))
        ex.setdefault(arm, []).append({
            'kind': kind, 'case_id': r['case_id'], 'run_id': r['run_id'],
            **case_meta.get(r['case_id'], {}),
            'e9': round(e9, 3),
            'title': t, 'body': b,
            'scores': {**{d: r['score']['effect'][d] for d in EFFECT},
                       **{d: r['score']['safety'][d] for d in SAFETY}},
            'whys': {**r['whys'].get('effect', {}), **r['whys'].get('safety', {})},
        })
    for arm in ex:
        ex[arm].sort(key=lambda x: x['kind'] == 'worst')
    return ex


def build_baseline(cells, case_meta):
    """judge_baseline.jsonl 存在 → B0/B1 均分 + 每 LLM arm vs B0/B1 paired 对比; 否则 None。"""
    if not BASELINE_JUDGE.exists():
        return None
    bl_cells = {}   # bl → {case: e9}
    bl_comp = {}
    for l in BASELINE_JUDGE.open():
        r = json.loads(l)
        bl = r['gen_model']
        e9, _, comp = per_output_scores(r)
        bl_cells.setdefault(bl, {})[r['case_id']] = e9
        bl_comp.setdefault(bl, []).append(comp)
    out = {'levels': {}, 'comparisons': []}
    for bl, by_case in sorted(bl_cells.items()):
        out['levels'][bl] = {'e9': round(statistics.mean(by_case.values()), 3),
                             'n': len(by_case),
                             'pass_rate': round(sum(bl_comp[bl]) / len(bl_comp[bl]), 4)}
    n_tests = len(cells) * len(bl_cells)
    for (m, v), by_case in sorted(cells.items()):
        for bl, bcells in sorted(bl_cells.items()):
            common = sorted(set(by_case) & set(bcells))
            diffs = [by_case[c]['e9'] - bcells[c] for c in common]
            z, p, n = wilcoxon_signed_rank(diffs)
            p_adj = min(1.0, p * n_tests)
            out['comparisons'].append({'arm': f"{m}|{v}", 'baseline': bl,
                                       'delta': round(statistics.mean(diffs), 3),
                                       'p_adj': float(f"{p_adj:.3g}"), 'sig': p_adj < 0.05})
    return out


def main():
    cells = load_cells()
    ops = load_raw_ops()
    case_meta = load_case_meta()
    models = sorted(set(k[0] for k in cells))

    arms = []
    for m in models:
        for v in VARS:
            if (m, v) not in cells:
                continue
            by_case = cells[(m, v)]
            comp = [c for c in by_case.values() if c['comp']]
            o = ops.get((m, v), {})
            dims = {d: round(statistics.mean(c['dims'][d] for c in comp if d in c['dims']), 3)
                    for d in EFFECT}
            arms.append({'model': m, 'var': v, 'n_cells': len(by_case),
                         'pass_rate': round(len(comp) / len(by_case), 4),
                         'e9': round(statistics.mean(c['e9'] for c in comp), 3),
                         'e8': round(statistics.mean(c['e8'] for c in comp), 3),
                         'cost': round(o.get('cost_mean', 0), 5),
                         'lat_mean': round(o.get('lat_mean', 0), 1),
                         'lat_p95': round(o.get('lat_p95', 0), 1),
                         'len_ok': round(o.get('len_ok_rate', 0), 4),
                         'empty': o.get('empty', 0),
                         'dims': dims})

    rankings = {}
    for var in ['A', 'C']:
        order, pairs = pairwise(cells, var, models, 6)
        rankings[var] = {'order': order, 'pairs': pairs}

    ladders = {}
    for m, steps in MODEL_STEPS.items():
        lad = []
        prev = None
        n_tests = len(steps) - 1
        for v in steps:
            e9 = statistics.mean(c['e9'] for c in cells[(m, v)].values() if c['comp'])
            item = {'var': v, 'e9': round(e9, 3)}
            if prev:
                ca, cb = cells[(m, prev)], cells[(m, v)]
                common = sorted(set(ca) & set(cb))
                diffs = [cb[c]['e9'] - ca[c]['e9'] for c in common]
                z, p, n = wilcoxon_signed_rank(diffs)
                p_adj = min(1.0, p * n_tests)
                item.update({'delta': round(statistics.mean(diffs), 4), 'p_adj': float(f"{p_adj:.3g}"),
                             'sig': p_adj < 0.05})
            lad.append(item)
            prev = v
        ladders[m] = lad

    cd = {}
    for m in ['kimi-k2.6', 'gemini-2.5-flash']:
        cc, cdd = cells[(m, 'C')], cells[(m, 'D')]
        common = sorted(set(cc) & set(cdd))
        rows = []
        for dim in EFFECT:
            diffs = [cdd[c]['dims'][dim] - cc[c]['dims'][dim] for c in common
                     if dim in cdd[c]['dims'] and dim in cc[c]['dims']]
            z, p, n = wilcoxon_signed_rank(diffs)
            p_adj = min(1.0, p * 9)
            rows.append({'dim': dim, 'delta': round(statistics.mean(diffs), 4),
                         'p_adj': float(f"{p_adj:.3g}"), 'sig': p_adj < 0.05})
        rows.sort(key=lambda r: r['delta'])
        cd[m] = rows

    boot = []
    for label, ax, ay in [
            ("gpt−kimi @A", ('gpt-5.5', 'A'), ('kimi-k2.6', 'A')),
            ("gpt−gemini @A", ('gpt-5.5', 'A'), ('gemini-2.5-flash', 'A')),
            ("gpt−kimi @C", ('gpt-5.5', 'C'), ('kimi-k2.6', 'C')),
            ("kimi D−C", ('kimi-k2.6', 'D'), ('kimi-k2.6', 'C')),
            ("gemini D−C", ('gemini-2.5-flash', 'D'), ('gemini-2.5-flash', 'C')),
            ("kimi C−A", ('kimi-k2.6', 'C'), ('kimi-k2.6', 'A'))]:
        ex_ = {c: v['e9'] for c, v in cells[ax].items()}
        ey = {c: v['e9'] for c, v in cells[ay].items()}
        d, lo, hi = cluster_bootstrap_ci(ex_, ey)
        boot.append({'label': label, 'delta': round(d, 4), 'lo': round(lo, 4), 'hi': round(hi, 4),
                     'excl0': lo > 0 or hi < 0})

    eligible = [a for a in arms if a['pass_rate'] >= GATE]
    for a in eligible:
        a['nondom'] = not any(
            (b['e9'] >= a['e9'] and b['cost'] <= a['cost'] and b['lat_p95'] <= a['lat_p95'])
            and (b['e9'] > a['e9'] or b['cost'] < a['cost'] or b['lat_p95'] < a['lat_p95'])
            for b in eligible if b is not a)

    n_judged = sum(1 for _ in JUDGE.open())
    data = {
        'generated_at': str(date.today()),
        'totals': {'n_judged': n_judged, 'judge_cost': 199.41, 'n_cells': sum(a['n_cells'] for a in arms),
                   'n_videos': 32, 'n_personas': 13, 'n_models': len(models),
                   'judge': {'zh': 'doubao-seed-2-0-lite (主评全量, 抽样双评校准)',
                             'en': 'doubao-seed-2-0-lite (full main run, sampled second-judge calibration)'}},
        'arms': arms,
        'rankings': rankings,
        'ladders': ladders,
        'cd_decomp': cd,
        'boot_cis': boot,
        'gate': GATE,
        'baseline': build_baseline(cells, case_meta),
        'decision': json.loads(DECISION.read_text(encoding='utf-8')) if DECISION.exists() else None,
        'copy_examples': build_copy_examples(cells, case_meta, (json.loads(DECISION.read_text(encoding='utf-8'))['primary_5_3'] if DECISION.exists() else 'gpt-5.5|A')),
        'examples': build_examples(cells, case_meta),
        'notes': {
            'correction': {
                'zh': 'content_fidelity 已做 −0.45 系统偏差校正 (72 条人工裁决锚定)',
                'en': 'content_fidelity carries a −0.45 systematic-bias correction (anchored on 72 human-adjudicated items)'},
            'readability': {
                'zh': 'readability 16185/16185 全 5 分 (天花板, 无区分度) — 排名同报 8 维口径对照',
                'en': 'readability scores 5/5 on all 16,185 outputs (ceiling, no discriminative power) — rankings are also reported on the 8-dimension basis for comparison'},
            'latency': {
                'zh': '耗时为本项目测量环境实测 (含跨太平洋网络), 仅作环境内相对比较, 不代表 vendor 生产延迟; kimi|D p95 主要是内联视频上传耗时',
                'en': 'latency is measured in this project\'s own environment (including a cross-Pacific network hop) and is only comparable within it, not a statement about vendor production latency; kimi|D p95 is dominated by inline video upload'},
            'effect_scope': {
                'zh': '效果分 = 合规 cells 均值 (不合规 cell 不进 Pareto); 成本/耗时 = 全 outputs (生产口径)',
                'en': 'quality score = mean over compliant cells (non-compliant cells are excluded from the Pareto front); cost and latency = all outputs (production basis)'},
        },
    }
    OUT.write_text("window.RESULTS_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding='utf-8')
    build_standalone()
    kb = OUT.stat().st_size / 1024
    print(f"[OK] {OUT} ({kb:.0f}KB) | arms {len(arms)} | examples {sum(len(v) for v in data['examples'].values())}"
          f" | baseline {'✅已算' if data['baseline'] else '未评(置null, 前端显示待跑)'}"
          f" | decision {'✅' if data['decision'] else '未跑'}")



def build_copy_examples(cells, case_meta, primary_arm="gpt-5.5|A", n=3):
    """现状 B0 / 规则 B1 / 推荐配置 LLM 的同 case 文案三方对比 (业务版最有说服力的素材)。

    选样避免挑好的: 按 (LLM − B0) 效果差排序取**中位数附近**的 n 条, 代表典型而非最佳。
    """
    if not BASELINE_JUDGE.exists():
        return None
    m, v = primary_arm.split('|')
    if (m, v) not in cells:
        return None
    # baseline 文案 + 分数
    bl_text, bl_score = {}, {}
    for l in Path("results/baseline_rows.jsonl").open(encoding='utf-8'):
        r = json.loads(l)
        bl_text[(r['model'], r['case_id'])] = (r['output_title'], r['output_body'])
    for l in BASELINE_JUDGE.open(encoding='utf-8'):
        r = json.loads(l)
        bl_score[(r['gen_model'], r['case_id'])] = per_output_scores(r)[0]
    # 推荐 arm 的 LLM 文案 (取该 cell 三次重复里效果分中位的一条)
    llm_text = {}
    for l in Path(f"results/var_{v}_raw.jsonl").open(encoding='utf-8'):
        r = json.loads(l)
        if r['model'] == m:
            llm_text.setdefault(r['case_id'], []).append((r['run_id'], r['output_title'], r['output_body']))
    rows = []
    for case, c in cells[(m, v)].items():
        if (('B0', case) not in bl_score) or (('B1', case) not in bl_score) or case not in llm_text:
            continue
        rows.append({'case_id': case, 'gap': c['e9'] - bl_score[('B0', case)],
                     'llm_e9': c['e9'], 'b0_e9': bl_score[('B0', case)], 'b1_e9': bl_score[('B1', case)]})
    if len(rows) < n:
        return None
    rows.sort(key=lambda r: r['gap'])
    mid = len(rows) // 2
    picked = rows[max(0, mid - n // 2): max(0, mid - n // 2) + n]   # 中位数附近
    out = []
    for r in picked:
        case = r['case_id']
        runs = sorted(llm_text[case])
        _, lt, lb = runs[len(runs) // 2]
        out.append({**r, **case_meta.get(case, {}),
                    'b0': {'title': bl_text[('B0', case)][0], 'body': bl_text[('B0', case)][1], 'e9': round(r['b0_e9'], 2)},
                    'b1': {'title': bl_text[('B1', case)][0], 'body': bl_text[('B1', case)][1], 'e9': round(r['b1_e9'], 2)},
                    'llm': {'title': lt, 'body': lb, 'e9': round(r['llm_e9'], 2), 'arm': primary_arm},
                    'gap': round(r['gap'], 2)})
    return {'arm': primary_arm, 'n_total': len(rows), 'pick_rule': {'zh': '按 LLM−B0 效果差排序取中位数附近, 代表典型而非最佳',
                          'en': 'sorted by the LLM−B0 quality gap and picked around the median — typical cases, not the best ones'}, 'items': out}


def build_standalone():
    """把 data_results.js 内联进 dashboard, 产出单文件版 —— 双击即开, 可直接分享 (无需 http server)."""
    data = OUT.read_text(encoding="utf-8")
    tag = '<script src="data_results.js"></script>'
    for name in ("dashboard", "dashboard_biz"):
        src = Path(f"web/{name}.html")
        if not src.exists():
            continue
        html = src.read_text(encoding="utf-8")
        if tag not in html:
            print(f"[warn] {name}.html 未找到 data_results.js 引用, 跳过"); continue
        out = Path(f"web/{name}_standalone.html")
        out.write_text(html.replace(tag, f"<script>\n{data}\n</script>"), encoding="utf-8")
        print(f"[OK] {out} ({out.stat().st_size/1024:.0f}KB, 自包含单文件)")


if __name__ == '__main__':
    main()
