"""生成 + judge 数据质检 (data QA). 系统查: 条数/分布 / schema 回灌 / 重复 / 字段一致 /
文本异常 / 数值合理性 / 跨 var 一致性。可复用项目资产。

CLI: PYTHONPATH=src python src/qa_data.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from schemas import OutputRow, LLMJudgeScore

R = "results"
# (文件, 预期模型集, 预期总行) — var D kimi 还在涨, 预期标 None (只查质量不查满)
GEN_FILES = [
    (f"{R}/var_A_raw.jsonl", {"gemini-2.5-flash", "gpt-5.5", "kimi-k2.6", "deepseek-v4-pro"}, 4992, "A"),
    (f"{R}/var_B_raw.jsonl", {"gemini-2.5-flash", "gpt-5.5", "kimi-k2.6"}, 3744, "B"),
    (f"{R}/var_C_raw.jsonl", {"gemini-2.5-flash", "gpt-5.5", "kimi-k2.6", "deepseek-v4-pro"}, 4992, "C"),
    (f"{R}/var_D_raw.jsonl", {"gemini-2.5-flash"}, 1248, "D"),
    (f"{R}/var_D_kimi_raw.jsonl", {"kimi-k2.6"}, None, "D"),
]

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # 控制字符 (除 \t\n\r)


def has_anomaly_chars(s: str) -> bool:
    if CTRL_RE.search(s):
        return True
    if "�" in s:  # replacement char (编码损坏)
        return True
    return False


def qa_gen_file(path: str, exp_models: set, exp_total: int | None, exp_var: str):
    p = Path(path)
    print(f"\n{'='*70}\n{path}")
    if not p.exists():
        print("  ⚠️ 文件不存在"); return
    raw = [l for l in p.open(encoding="utf-8") if l.strip()]
    rows, bad_json = [], 0
    for l in raw:
        try:
            rows.append(json.loads(l))
        except json.JSONDecodeError:
            bad_json += 1
    n = len(rows)
    tot = f"{n}" + (f"/{exp_total} {'✅' if n == exp_total else '⚠️差'+str(exp_total-n)}" if exp_total else " (增长中)")
    print(f"  行数: {tot} | JSON 解析失败: {bad_json}")

    # 1. Pydantic 回灌 (字段+类型+值域最强检查)
    schema_fail = []
    for i, d in enumerate(rows):
        try:
            OutputRow(**d)
        except Exception as e:
            if len(schema_fail) < 3:
                schema_fail.append(f"行{i}: {str(e)[:80]}")
    print(f"  schema 回灌: {'✅ 全过' if not schema_fail else '❌ '+str(len(schema_fail))+'+ 失败: '+str(schema_fail)}")

    # 2. 重复 (cell_id, run_id)
    keys = [(d.get("cell_id"), d.get("run_id")) for d in rows]
    dup = n - len(set(keys))
    print(f"  重复 (cell_id,run_id): {'✅ 0' if dup == 0 else '❌ '+str(dup)}")

    # 3. 模型 / run_id 分布
    by_model = Counter(d.get("model") for d in rows)
    bad_model = set(by_model) - exp_models
    print(f"  模型分布: {dict(by_model)} {'✅' if not bad_model else '❌ 意外模型:'+str(bad_model)}")
    runs = Counter(d.get("run_id") for d in rows)
    print(f"  run_id 分布: {dict(sorted(runs.items()))} {'✅' if set(runs)<= {1,2,3} else '❌'}")

    # 4. var_label 与文件一致
    vbad = [d.get("var_label") for d in rows if d.get("var_label") != exp_var]
    print(f"  var_label 一致({exp_var}): {'✅' if not vbad else '❌ '+str(len(vbad))+' 条不符'}")

    # 5. cell_id 自洽 (= model|var|case_id)
    cbad = sum(1 for d in rows if d.get("cell_id") != f"{d.get('model')}|{d.get('var_label')}|{d.get('case_id')}")
    print(f"  cell_id 自洽: {'✅' if cbad == 0 else '❌ '+str(cbad)+' 条不符'}")

    # 6. 文本异常: 空 / 控制字符·乱码 / length_compliant 与实际不符
    empty_t = sum(1 for d in rows if not (d.get("output_title") or "").strip())
    empty_b = sum(1 for d in rows if not (d.get("output_body") or "").strip())
    anom = sum(1 for d in rows if has_anomaly_chars(d.get("output_title", "")+d.get("output_body", "")))
    lc_mismatch = sum(1 for d in rows
                      if d.get("length_compliant") != (len(d.get("output_title", "")) <= 50 and len(d.get("output_body", "")) <= 150))
    print(f"  空标题/正文: {empty_t}/{empty_b} | 异常字符/乱码: {'✅ 0' if anom==0 else '⚠️ '+str(anom)} | length_compliant 与实际不符: {'✅ 0' if lc_mismatch==0 else '❌ '+str(lc_mismatch)}")

    # 7. 数值合理性
    bad_num = sum(1 for d in rows if d.get("tokens_in", 0) <= 0 or d.get("tokens_out", 0) < 0
                  or d.get("cost_usd", 0) < 0 or d.get("latency_ms", 0) <= 0)
    print(f"  数值异常(token/cost/latency ≤0): {'✅ 0' if bad_num==0 else '⚠️ '+str(bad_num)}")

    # 8. parse_ok 比例 + var 专属
    parse_ok = sum(1 for d in rows if d.get("auto_evidence", {}).get("parse_ok"))
    print(f"  parse_ok: {parse_ok}/{n} ({parse_ok/n*100:.1f}%)" if n else "")
    if exp_var == "C":
        no_tr = sum(1 for d in rows if not d.get("preprocess_meta", {}).get("transcript_language"))
        print(f"  [var C] 无 transcript 元数据: {'✅ 0' if no_tr==0 else '⚠️ '+str(no_tr)}")
    if exp_var == "D":
        no_vid = sum(1 for d in rows if not d.get("auto_evidence", {}).get("video_attached"))
        print(f"  [var D] video_attached=False: {'✅ 0' if no_vid==0 else '❌ '+str(no_vid)}")
    return rows


def qa_cross(all_rows: dict):
    print(f"\n{'='*70}\n跨 var 一致性")
    cid2vid, cid2pid = {}, {}
    conflict = 0
    for var, rows in all_rows.items():
        for d in rows:
            c = d.get("case_id")
            if c in cid2vid and cid2vid[c] != d.get("video_id"):
                conflict += 1
            if c in cid2pid and cid2pid[c] != d.get("persona_id"):
                conflict += 1
            cid2vid[c] = d.get("video_id"); cid2pid[c] = d.get("persona_id")
    print(f"  同 case_id 的 video_id/persona_id 跨 var 一致: {'✅' if conflict==0 else '❌ '+str(conflict)+' 冲突'}")


def qa_judge(path: str, exp_main: str | None):
    p = Path(path)
    print(f"\n{'='*70}\n{path}")
    if not p.exists():
        print("  ⚠️ 不存在"); return
    rows = [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]
    print(f"  行数: {len(rows)}")
    keys = [(d["cell_id"], d["run_id"], d["judge_model"]) for d in rows]
    dup = len(rows) - len(set(keys))
    print(f"  重复 (cell,run,judge): {'✅ 0' if dup==0 else '❌ '+str(dup)}")
    sfail = []
    for d in rows:
        try:
            LLMJudgeScore(**{"judge_model": d["judge_model"], **d["score"]})
        except Exception as e:
            if len(sfail) < 2:
                sfail.append(str(e)[:70])
    print(f"  score 回灌 LLMJudgeScore: {'✅ 全过' if not sfail else '❌ '+str(sfail)}")
    jm = Counter(d["judge_model"] for d in rows)
    print(f"  judge_model 分布: {dict(jm)}")


def main():
    print("数据质检 (data QA)")
    all_rows = defaultdict(list)
    for path, models, tot, var in GEN_FILES:
        r = qa_gen_file(path, models, tot, var)
        if r:
            all_rows[path] = r
    qa_cross(all_rows)
    qa_judge(f"{R}/judge_main_kappa.jsonl", "doubao")
    qa_judge(f"{R}/judge_aux_kappa.jsonl", None)
    print(f"\n{'='*70}\n质检完成。")


if __name__ == "__main__":
    main()
