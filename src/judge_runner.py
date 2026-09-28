"""W3 judge runner — 仿 run_generation.run_full (并发 + 落盘 + 断点续跑 + parse-fail 重试).

judge 池已锁 (doc §4.3 Step1 + worklog 2026-06-03):
  主评 = Doubao-2.0-lite 评全部 output (非生成模型 → 无自评/无裁判混淆; 最便宜).
  副评 = Gemini-2.5-flash (评非 gemini output) + Kimi (评 gemini output, 避自评), 仅 κ 抽样子集.

视频传输 (probe 全验通, 直接复用): Gemini Files API; Doubao/Kimi 内联 base64 video_url.

落盘: 独立 JudgeRow jsonl (不动 raw 生成文件); 下游 W3 聚合时按 (cell_id, run_id, judge_model) merge.
键名映射: judge 输出 JSON 键 ≠ schema 字段名 (JUDGE_SYSTEM vs schemas.py), 落盘前映射 + Pydantic 校验.

CLI:
  主评: PYTHONPATH=src python src/judge_runner.py --mode main --inputs results/var_A_raw.jsonl ... [--limit N]
  副评: PYTHONPATH=src python src/judge_runner.py --mode aux  --inputs ... --sample N
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import judge_prompt as JP
from judge_probe import judge_gemini, judge_kimi, parse_judge_json
from doubao_judge_probe import judge_doubao
from run_generation import render_user_prompt
from schemas import SafetyScore, EffectScore, LLMJudgeScore

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

TEST_CASES = Path("data/test_cases.jsonl")

# judge 池
MAIN_JUDGE = "doubao-seed-2-0-lite-260428"  # 主评 (全量)
AUX_GEMINI = "gemini-2.5-flash"             # 副评: 评非 gemini output
AUX_KIMI = "kimi-k2.6"                      # 副评: 评 gemini output (避自评)
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

# judge 价 (USD per 1M token).
#  - gemini/kimi: design-doc §4.2 同表 (judge_probe.PRICES 同源)
#  - doubao lite: 火山官方 0.6/3.6 元·M, >32k tier ×1.5 → 0.9/5.4 元·M; 汇率 ~7.2 (worklog 2026-06-03 实测)
JUDGE_PRICES = {
    "gemini-2.5-flash": (0.30, 2.50),
    "kimi-k2.6": (1.00, 3.00),
    MAIN_JUDGE: (0.90 / 7.2, 5.40 / 7.2),  # ≈ (0.125, 0.750)
}

# judge JSON 键 (JUDGE_SYSTEM) → schema 字段 (schemas.py). 不同名的 4 处是关键.
SAFETY_KEY_MAP = {
    "misleading": "misleading",
    "manipulation": "manipulative",      # ← 不同名
    "harmful": "harmful",
    "privacy": "privacy",
}
EFFECT_KEY_MAP = {
    "readability": "readability",
    "video_relevance": "video_relevance",
    "faithfulness": "content_fidelity",       # ← 不同名
    "expressiveness": "expressiveness",
    "naturalness": "naturalness",
    "expectation_match": "expectation_alignment",  # ← 不同名
    "preference_match": "preference_match",
    "tone_fit": "tone_fit",
    "interruption_value": "push_value",       # ← 不同名
}

_DOUBAO_CLIENT = None
_DOUBAO_LOCK = threading.Lock()


def doubao_client() -> OpenAI:
    global _DOUBAO_CLIENT
    with _DOUBAO_LOCK:
        if _DOUBAO_CLIENT is None:
            # timeout=120 + max_retries=0: 慢请求快速失败, 靠 judge_one 自己重试 (防偶发慢请求用默认 600s 卡死 worker)
            _DOUBAO_CLIENT = OpenAI(api_key=os.environ["DOUBAO_API_KEY"], base_url=DOUBAO_BASE_URL,
                                    timeout=120, max_retries=0)
        return _DOUBAO_CLIENT


def to_judge_score(judge_model: str, obj: dict) -> LLMJudgeScore:
    """judge 输出 JSON → LLMJudgeScore (键名映射 + Pydantic 校验; 越界/缺键会抛错 → 当 parse-fail 重试)."""
    s = obj["safety"]
    e = obj["effect"]
    safety = SafetyScore(**{
        schema_k: bool(s[json_k]["violation"]) for json_k, schema_k in SAFETY_KEY_MAP.items()
    })
    eff_kwargs = {}
    for json_k, schema_k in EFFECT_KEY_MAP.items():
        v = e[json_k]
        eff_kwargs[schema_k] = v.get("score") if isinstance(v, dict) else v  # preference_match 可 None
    effect = EffectScore(**eff_kwargs)
    return LLMJudgeScore(judge_model=judge_model, safety=safety, effect=effect)


def extract_whys(obj: dict) -> dict:
    """从 judge 原始输出提取每维 why (理由), 键名映射到 schema 名。
    旁路存 (不进 LLMJudgeScore schema), 给可解释性: 全量主评每条带 judge 打分理由, 质量分非黑盒。"""
    s, e = obj.get("safety", {}), obj.get("effect", {})
    def w(v): return ((v.get("why") if isinstance(v, dict) else "") or "").strip()
    return {
        "safety": {sk: w(s.get(jk, {})) for jk, sk in SAFETY_KEY_MAP.items()},
        "effect": {sk: w(e.get(jk, {})) for jk, sk in EFFECT_KEY_MAP.items()},
    }


def run_one_judge(judge_model: str, youtube_id: str, user_text: str) -> dict:
    """dispatch 到对应 vendor 视频调用 (probe 已验). 返回 {text, tokens_in, tokens_out}."""
    if judge_model.startswith("gemini"):
        return judge_gemini(judge_model, youtube_id, user_text)
    if judge_model.startswith("doubao"):
        return judge_doubao(doubao_client(), youtube_id, user_text)
    return judge_kimi(judge_model, youtube_id, user_text)


def cost(judge_model: str, tin: int, tout: int) -> float:
    pi, po = JUDGE_PRICES[judge_model]
    return (tin * pi + tout * po) / 1e6


# ─────────────────────────────────────────────────────────
# 加载 output + cases + 配对
# ─────────────────────────────────────────────────────────

def load_cases() -> dict:
    return {json.loads(l)["case_id"]: json.loads(l) for l in TEST_CASES.open(encoding="utf-8")}


def load_outputs(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        if not p.exists():
            print(f"  [warn] 输入文件不存在, 跳过: {p}")
            continue
        for l in p.open(encoding="utf-8"):
            try:
                rows.append(json.loads(l))
            except json.JSONDecodeError:
                continue
    return rows


def aux_judge_for(gen_model: str) -> str | None:
    """副评配对 (避自评): gemini output → kimi 评; 非 gemini output → gemini 评."""
    if gen_model.startswith("gemini"):
        return AUX_KIMI
    return AUX_GEMINI


def judge_one(out_row: dict, judge_model: str, role: str, cases: dict, max_retries: int = 2) -> dict | None:
    """评一条 output → JudgeRow dict (落盘用). 失败重试; 仍失败返回 None (留待续跑)."""
    case = cases[out_row["case_id"]]
    persona = case["persona"]
    youtube_id = case["video"]["youtube_id"]
    var = out_row.get("var_label", "A")
    var_input = render_user_prompt(case, var)  # generator 当时看到的 (作 judge context)
    user_text = JP.build_judge_user_prompt(
        persona, var_input, out_row["output_title"], out_row["output_body"])
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            r = run_one_judge(judge_model, youtube_id, user_text)
            latency = int((time.time() - t0) * 1000)
            obj, ok = parse_judge_json(r["text"])
            if not ok:
                raise ValueError("judge JSON parse 失败")
            score = to_judge_score(judge_model, obj)  # 键名映射 + schema 校验 (越界→抛错→重试)
            return {
                "cell_id": out_row["cell_id"],
                "case_id": out_row["case_id"],
                "run_id": out_row["run_id"],
                "gen_model": out_row["model"],
                "var": var,
                "judge_model": judge_model,
                "role": role,
                "score": score.model_dump(),
                "whys": extract_whys(obj),  # judge 每维理由 (可解释性, 旁路存)
                "tokens_in": r["tokens_in"],
                "tokens_out": r["tokens_out"],
                "latency_ms": latency,
                "cost_usd": cost(judge_model, r["tokens_in"], r["tokens_out"]),
                "judged_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 ** attempt * 2)
    print(f"  [FAIL] {judge_model}|{out_row['cell_id']}|r{out_row['run_id']}: "
          f"{type(last_err).__name__}: {str(last_err)[:90]}")
    return None


def _load_done(out_path: Path) -> set[tuple[str, int, str]]:
    """已落盘的 (cell_id, run_id, judge_model), 续跑跳过."""
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                d = json.loads(line)
                done.add((d["cell_id"], d["run_id"], d["judge_model"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def build_tasks(mode: str, outputs: list[dict], sample: int, limit: int,
                only_judge: str | None = None) -> list[tuple[dict, str, str]]:
    """(out_row, judge_model, role) 列表.
    main: 主评 Doubao 评全部 (可 --limit 截断 smoke). aux: 副评配对, 每 gen_model 抽 sample 条.
    only_judge: aux 时只保留配对到该 judge 的 task (如 gemini 配额挂时只跑 kimi 半边).
    """
    tasks = []
    if mode == "main":
        rows = outputs[:limit] if limit else outputs
        tasks = [(o, MAIN_JUDGE, "main") for o in rows]
    elif mode == "aux":
        # 按 gen_model 分组, 各抽 sample 条 (确定性: 取前 sample, 落盘顺序稳定)
        by_model: dict[str, list[dict]] = {}
        for o in outputs:
            by_model.setdefault(o["model"], []).append(o)
        for gm, rows in by_model.items():
            jm = aux_judge_for(gm)
            if only_judge and jm != only_judge:
                continue
            picked = rows[:sample] if sample else rows
            tasks += [(o, jm, "aux") for o in picked]
    return tasks


def run(mode: str, input_paths: list[Path], out_path: Path, workers: int, sample: int, limit: int,
        only_judge: str | None = None, sort_size: bool = False):
    cases = load_cases()
    outputs = load_outputs(input_paths)
    # 跳过空输出 (无文案可评, 如 gemini 对 V07 等视频 block_reason=OTHER 返回空)。
    # 注意: 只是 judge 不评, 原始数据文件保留不删 (空输出本身是一个结果/证据)。
    n_before = len(outputs)
    outputs = [o for o in outputs if (o.get("output_body") or "").strip()]
    n_empty = n_before - len(outputs)
    if n_empty:
        print(f"[judge] 跳过 {n_empty} 条空输出 (无文案可评; 数据保留不删, 分析时标 N/A)")
    if sort_size:  # 从小到大: 小视频先评(快+稳), 大的后; 配 wrapper 分批 = 从小到大累进
        vdir = Path("data/videos")
        def _vsize(o):
            p = vdir / f"{cases[o['case_id']]['video']['youtube_id']}.mp4"
            return p.stat().st_size if p.exists() else 0
        outputs.sort(key=_vsize)
        print(f"[judge] --sort-size: 按视频文件大小从小到大排序 {len(outputs)} 条")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(out_path)
    all_tasks = build_tasks(mode, outputs, sample, limit, only_judge)
    tasks = [(o, j, r) for (o, j, r) in all_tasks
             if (o["cell_id"], o["run_id"], j) not in done]

    print(f"[judge] mode={mode} | 输入 output {len(outputs)} | 目标 judge call {len(all_tasks)}")
    print(f"[judge] 已完成 {len(done)} (续跑跳过), 本轮跑 {len(tasks)}, 并发 {workers} → {out_path}")
    if not tasks:
        print("[judge] 全部已完成, 无需跑.")
        return

    lock = threading.Lock()
    n_ok = n_fail = 0
    cost_sum = 0.0
    t0 = time.time()
    with out_path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(judge_one, o, j, r, cases): (o["cell_id"], o["run_id"], j)
                for (o, j, r) in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            with lock:
                if row is None:
                    n_fail += 1
                else:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                    n_ok += 1
                    cost_sum += row["cost_usd"]
            if i % 10 == 0 or i == len(tasks):
                el = time.time() - t0
                rate = i / el if el else 0
                eta = (len(tasks) - i) / rate if rate else 0
                print(f"  {i}/{len(tasks)} | ok={n_ok} fail={n_fail} | ${cost_sum:.2f} | "
                      f"{rate:.2f}/s | ETA {eta/60:.0f}min")
    print(f"\n[OK] mode={mode} ok={n_ok} fail={n_fail} | 成本 ${cost_sum:.2f} | "
          f"用时 {(time.time()-t0)/60:.1f}min → {out_path}")
    if n_fail:
        print(f"     {n_fail} 条失败 (未落盘); 再跑一次本命令会续跑 (resume)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["main", "aux"], required=True)
    ap.add_argument("--inputs", nargs="+", required=True, help="output jsonl 文件 (var_*_raw.jsonl)")
    ap.add_argument("--out", default=None, help="落盘路径 (默认 results/judge_{mode}_raw.jsonl)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sample", type=int, default=0, help="aux: 每 gen_model 抽几条 (0=全部)")
    ap.add_argument("--limit", type=int, default=0, help="main: 只评前 N 条 (0=全部, smoke 用)")
    ap.add_argument("--only-judge", default=None, help="aux: 只跑配对到该 judge 的 (如 kimi-k2.6; gemini 配额挂时用)")
    ap.add_argument("--sort-size", action="store_true", help="main: 按视频文件大小从小到大评 (小先, 快+稳)")
    args = ap.parse_args()
    out = Path(args.out) if args.out else Path(f"results/judge_{args.mode}_raw.jsonl")
    run(args.mode, [Path(p) for p in args.inputs], out, args.workers, args.sample, args.limit,
        args.only_judge, args.sort_size)


if __name__ == "__main__":
    main()
