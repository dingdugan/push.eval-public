"""κ pilot 分层抽样 (doc §4.3 Step2): 从 var A/B/C output 抽代表性子集供双评算一致性.

分层 = gen_model × lifecycle (× var 轮转), 确定性可复现 (按 cell_id 排序取均匀间隔, 不用随机).
目标 ~120 条: 覆盖 4 生成模型 / 5 lifecycle (含 cold-start 测 preference_match=null) / 3 var.
每 cell 固定取 run_id=1 (κ 只需单 output 代表, 不需 3 rep).

CLI: PYTHONPATH=src python src/kappa_sample.py [--per-cell 6 --out results/kappa_sample.jsonl]
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

TEST_CASES = Path("data/test_cases.jsonl")
INPUTS = ["results/var_A_raw.jsonl", "results/var_B_raw.jsonl", "results/var_C_raw.jsonl"]


def load_cases() -> dict:
    return {json.loads(l)["case_id"]: json.loads(l) for l in TEST_CASES.open(encoding="utf-8")}


def even_pick(items: list, k: int) -> list:
    """确定性: 排序后按均匀间隔取 k 条 (分散覆盖, 可复现)."""
    if len(items) <= k:
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-cell", type=int, default=6, help="每 (model,lifecycle) 抽几条")
    ap.add_argument("--out", default="results/kappa_sample.jsonl")
    args = ap.parse_args()

    cases = load_cases()
    # 收集 run_id=1 的 output, 按 (model, lifecycle) 分桶
    buckets: dict[tuple, list] = collections.defaultdict(list)
    for path in INPUTS:
        p = Path(path)
        if not p.exists():
            print(f"  [warn] 缺 {path}, 跳过")
            continue
        for l in p.open(encoding="utf-8"):
            d = json.loads(l)
            if d.get("run_id") != 1:
                continue
            lc = cases[d["case_id"]]["persona"]["lifecycle"]
            buckets[(d["model"], lc)].append(d)

    # 每桶内按 var 轮转 + cell_id 排序后均匀取, 保证 var 覆盖
    sample = []
    for (model, lc), rows in sorted(buckets.items()):
        rows.sort(key=lambda r: (r.get("var_label", "A"), r["cell_id"]))
        sample += even_pick(rows, args.per_cell)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for d in sample:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # 汇总分布
    by_model = collections.Counter(d["model"] for d in sample)
    by_life = collections.Counter(cases[d["case_id"]]["persona"]["lifecycle"] for d in sample)
    by_var = collections.Counter(d.get("var_label", "A") for d in sample)
    print(f"[kappa_sample] 抽 {len(sample)} 条 → {out}")
    print(f"  gen_model: {dict(by_model)}")
    print(f"  lifecycle: {dict(by_life)}")
    print(f"  var:       {dict(by_var)}")
    # 副评配对预览 (避自评): gemini output→kimi 评; 非 gemini→gemini 评
    n_gem = sum(1 for d in sample if d["model"].startswith("gemini"))
    print(f"  副评配对: gemini-output {n_gem} 条→Kimi 评 (今天能跑); "
          f"非 gemini {len(sample)-n_gem} 条→Gemini 评 (等配额)")


if __name__ == "__main__":
    main()
