"""W4 baseline 底表生成 (B0 现状 + B1 规则启发式), design-doc §2.4 / §4.4。

B0/B1 = string template, 不消耗 LLM API; 每 case 一条 (内容 per video 相同, 但每个
persona 上下文都要评, 因为 preference_match / tone_fit 是 persona 相关维度)。
底表字段对齐 judge_runner.judge_one 的输入要求, 可直接:
  PYTHONPATH=src .venv/bin/python src/judge_runner.py --mode main \
    --inputs results/baseline_rows.jsonl --out results/judge_baseline.jsonl \
    --workers 4 --sort-size
  (~832 judge calls ≈ $10, Doubao+视频 → 公司网络跑)

模板 ground 在 doc §2.4 L333-334, 两处字段映射 + 一处语言适配 (均已 flag, owner 可否决):
  - creator_name → 数据实际字段 channel; vertical → category
  - B1 doc 原文是中文模板, 但语料/模型 output 全英文 → 英文化同结构
    (中文 baseline 在英文语料上会因语言错配天然低分, 反而虚高 LLM-vs-B1 增益)

CLI: PYTHONPATH=src python src/gen_baselines.py
"""
from __future__ import annotations
import json
from pathlib import Path

CASES = Path("data/test_cases.jsonl")
OUT = Path("results/baseline_rows.jsonl")


def baseline_outputs(video: dict) -> dict[str, tuple[str, str]]:
    ch = video["channel"]
    cat = video["category"]
    title = video["video_title"]
    return {
        # B0 现状: creator 直发 + 系统不改写 (doc §1.2 反例)
        "B0": (f"{ch} just posted:", title),
        # B1 规则启发式: metadata 拼装保守模板 (doc 中文模板英文化, 结构一致)
        "B1": (f"New video | {cat} · {ch}", f"{title} — from a {cat} creator you might like"),
    }


def main():
    rows = []
    for l in CASES.open(encoding="utf-8"):
        case = json.loads(l)
        for bl, (t, b) in baseline_outputs(case["video"]).items():
            rows.append({
                "cell_id": f"{bl}|A|{case['case_id']}",
                "case_id": case["case_id"],
                "run_id": 1,                      # baseline 不变 → 只评 1 次
                "model": bl,
                "var_label": "A",                 # judge context 用 var A (B0/B1 只用 metadata)
                "video_id": case["video"]["video_id"],
                "persona_id": case["persona"]["persona_id"],
                "output_title": t,
                "output_body": b,
            })
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    n_case = len(rows) // 2
    print(f"[OK] {len(rows)} 行 ({n_case} case × B0/B1) → {OUT}")
    print("样例 B0:", json.dumps(rows[0]["output_title"] + " / " + rows[0]["output_body"], ensure_ascii=False)[:110])
    print("样例 B1:", json.dumps(rows[1]["output_title"] + " / " + rows[1]["output_body"], ensure_ascii=False)[:110])


if __name__ == "__main__":
    main()
