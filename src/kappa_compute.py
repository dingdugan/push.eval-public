"""κ 一致性计算 (doc §4.3 Step2): 主评 vs 副评 在交集上的 inter-rater agreement.

safety 4 维 (binary): Cohen's κ (unweighted).
effect 9 维 (1-5 ordinal): Quadratic Weighted Kappa (QWK).
preference_match: 跳过任一为 null 的对 (cold-start).
达标线: κ ≥ 0.6 (doc §4.3). 不达标 → 调 prompt 或该维度全量双评.

按 aux judge 分组报 (Doubao-vs-Gemini / Doubao-vs-Kimi 各一套). 手写 κ (不依赖 sklearn).

CLI: PYTHONPATH=src python src/kappa_compute.py --main results/judge_main_kappa.jsonl --aux results/judge_aux_kappa.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

SAFETY = ["misleading", "manipulative", "harmful", "privacy"]
EFFECT = ["readability", "video_relevance", "content_fidelity", "expressiveness",
          "naturalness", "expectation_alignment", "preference_match", "tone_fit", "push_value"]


def cohen_kappa(a: list, b: list, labels: list, weights: str | None = None) -> float | None:
    """手写 Cohen's κ. weights=None(binary/nominal) 或 'quadratic'(QWK ordinal). 无变异→None."""
    if len(a) < 2:
        return None
    idx = {v: i for i, v in enumerate(labels)}
    k = len(labels)
    O = np.zeros((k, k))
    for x, y in zip(a, b):
        O[idx[x], idx[y]] += 1
    n = O.sum()
    if n == 0:
        return None
    row = O.sum(1)
    col = O.sum(0)
    E = np.outer(row, col) / n
    if weights == "quadratic":
        w = np.zeros((k, k))
        for i in range(k):
            for j in range(k):
                w[i, j] = (i - j) ** 2 / (k - 1) ** 2
        denom = (w * E).sum()
        if denom == 0:
            return None  # 一方无变异
        return 1 - (w * O).sum() / denom
    # unweighted
    po = np.trace(O) / n
    pe = (row * col).sum() / n ** 2
    if pe >= 1.0:
        return None  # 退化 (全同一类)
    return (po - pe) / (1 - pe)


def load(path: Path) -> dict:
    """(cell_id, run_id) → row."""
    by = {}
    if path.exists():
        for l in path.open(encoding="utf-8"):
            try:
                d = json.loads(l)
                by[(d["cell_id"], d["run_id"])] = d
            except (json.JSONDecodeError, KeyError):
                continue
    return by


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="results/judge_main_kappa.jsonl")
    ap.add_argument("--aux", default="results/judge_aux_kappa.jsonl")
    args = ap.parse_args()

    main_by = load(Path(args.main))
    aux_by = load(Path(args.aux))
    print(f"主评 {len(main_by)} 条 | 副评 {len(aux_by)} 条")

    # 按 aux judge_model 分组配对 (只在主+副都评了的交集上)
    groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for key, arow in aux_by.items():
        mrow = main_by.get(key)
        if mrow:
            groups[arow["judge_model"]].append((mrow, arow))

    if not groups:
        print("⚠️ 主副无交集 (副评还没数据?). 先跑 judge_runner --mode aux.")
        return

    for aux_judge, pairs in sorted(groups.items()):
        print(f"\n{'='*64}\n主评 Doubao  vs  副评 {aux_judge}   (n={len(pairs)} 配对)\n{'='*64}")
        print(f"{'维度':<22}{'κ':>8}  {'n':>4}  达标(≥0.6)")
        # safety binary κ
        for dim in SAFETY:
            a = [bool(m["score"]["safety"][dim]) for m, _ in pairs]
            b = [bool(x["score"]["safety"][dim]) for _, x in pairs]
            kp = cohen_kappa(a, b, [False, True])
            flag = "—(无变异)" if kp is None else ("✅" if kp >= 0.6 else "❌")
            ks = "  N/A" if kp is None else f"{kp:6.3f}"
            print(f"  [safety] {dim:<13}{ks}  {len(a):>4}  {flag}")
        # effect QWK
        for dim in EFFECT:
            a, b = [], []
            for m, x in pairs:
                va = m["score"]["effect"][dim]
                vb = x["score"]["effect"][dim]
                if va is None or vb is None:  # preference_match cold-start
                    continue
                a.append(int(va)); b.append(int(vb))
            kp = cohen_kappa(a, b, [1, 2, 3, 4, 5], weights="quadratic")
            flag = "—(无变异/样本少)" if kp is None else ("✅" if kp >= 0.6 else "❌")
            ks = "  N/A" if kp is None else f"{kp:6.3f}"
            print(f"  [effect] {dim:<13}{ks}  {len(a):>4}  {flag}")


if __name__ == "__main__":
    main()
