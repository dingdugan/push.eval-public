"""W3: 缓存分数等价性验证 (开 judge 缓存的唯一真风险点).

问题: 引用缓存视频跑出的分, 跟每次新传视频跑出的分, 是不是一样?
难点: gemini 默认 temperature=1.0, 同输入重复跑分数本就抖 (采样噪声). 不能拿
"缓存1次 vs 无缓存1次 不同" 当缓存有问题——那可能只是抽签波动.

受控设计: 同一条 output, 无缓存重复 ×R vs 缓存重复 ×R. 若缓存的分布落在无缓存
自身的 run-to-run 抖动范围内 → 缓存对打分透明 (无额外漂移), 方法上可用.

只 gemini-2.5-flash (kimi 缓存是另一套 API, 且慢, 不在此验). 2 output × (3+3) = 12 call.
CLI: PYTHONPATH=src .venv/bin/python src/judge_cache_equiv.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

import judge_prompt as JP
from judge_probe import load_cases, load_output, parse_judge_json
from run_generation import render_user_prompt

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

VIDEO_DIR = Path("data/videos")
MODEL = "gemini-2.5-flash"
REPS = 3
PICKS = [("V01_P1", "gpt-5.5"), ("V01_P5", "deepseek-v4-pro")]
EFFECT_KEYS = ["readability", "video_relevance", "faithfulness", "expressiveness",
               "naturalness", "expectation_match", "preference_match", "tone_fit",
               "interruption_value"]


def _scores(obj: dict) -> dict:
    """抠 9 效果维分数 (塌成 null 的记 None) + safety 违规集合."""
    eff = obj.get("effect", {})
    sc = {}
    for k in EFFECT_KEYS:
        v = eff.get(k)
        sc[k] = (v.get("score") if isinstance(v, dict) else v)
    viol = tuple(sorted(k for k, v in obj.get("safety", {}).items()
                        if isinstance(v, dict) and v.get("violation")))
    return sc, viol


def _gen(client, contents, cfg):
    from google.genai import errors as genai_errors
    for attempt in range(5):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=cfg)
        except genai_errors.ServerError:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)


def _gen_parsed(client, contents, cfg, max_parse_retry=3):
    """跑到拿合法 JSON 为止 (parse 失败也重试: 截断/503-partial 是一次性的). 返回 (scores, viol) 或 None."""
    for _ in range(max_parse_retry):
        r = _gen(client, contents, cfg)
        obj, ok = parse_judge_json(r.text or "")
        if ok and obj.get("effect") and obj.get("safety"):
            return _scores(obj)
    return None


def main():
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cases = load_cases()
    youtube_id = cases["V01_P1"]["video"]["youtube_id"]
    path = VIDEO_DIR / f"{youtube_id}.mp4"

    print("=" * 72)
    print(f"缓存分数等价性验证 — {MODEL}, {path.name}, 每条 无缓存×{REPS} vs 缓存×{REPS}")
    print("=" * 72)

    # 上传视频
    f = client.files.upload(file=str(path))
    for _ in range(60):
        f = client.files.get(name=f.name)
        if f.state == "ACTIVE":
            break
        if f.state == "FAILED":
            raise RuntimeError("FAILED")
        time.sleep(2)
    print(f"[视频 ACTIVE] {f.name}")

    # 建 cache
    cache = client.caches.create(
        model=MODEL,
        config=types.CreateCachedContentConfig(
            contents=[f], system_instruction=JP.JUDGE_SYSTEM, ttl="1200s"),
    )
    print(f"[cache] {cache.name} (token={cache.usage_metadata.total_token_count})\n")

    cfg_nocache = types.GenerateContentConfig(
        system_instruction=JP.JUDGE_SYSTEM, max_output_tokens=8000,
        response_mime_type="application/json")
    cfg_cache = types.GenerateContentConfig(
        cached_content=cache.name, max_output_tokens=8000,
        response_mime_type="application/json")

    verdicts = []
    for case_id, gen_model in PICKS:
        case = cases[case_id]
        out = load_output(case_id, gen_model)
        var_input = render_user_prompt(case, "A")
        user_text = JP.build_judge_user_prompt(
            case["persona"], var_input, out["output_title"], out["output_body"])

        runs = {"无缓存": [], "缓存": []}
        nfail = {"无缓存": 0, "缓存": 0}
        for _ in range(REPS):
            s = _gen_parsed(client, [f, user_text], cfg_nocache)
            (runs["无缓存"].append(s) if s else nfail.__setitem__("无缓存", nfail["无缓存"] + 1))
        for _ in range(REPS):
            s = _gen_parsed(client, [user_text], cfg_cache)
            (runs["缓存"].append(s) if s else nfail.__setitem__("缓存", nfail["缓存"] + 1))

        print(f"── [{case_id}] {gen_model} ── (parse 失败: 无缓存{nfail['无缓存']} 缓存{nfail['缓存']})")
        nc_viol = [v for _, v in runs["无缓存"]]
        c_viol = [v for _, v in runs["缓存"]]
        print(f"  safety 违规: 无缓存{nc_viol} | 缓存{c_viol}")
        print(f"  {'维度':18}{'无缓存(3次)':>16}{'缓存(3次)':>14}{'判定':>8}")
        drift_flags = []
        for k in EFFECT_KEYS:
            nc = [s[k] for s, _ in runs["无缓存"] if s[k] is not None]
            c = [s[k] for s, _ in runs["缓存"] if s[k] is not None]
            nc_str = "/".join(str(x) for x in nc) or "null"
            c_str = "/".join(str(x) for x in c) or "null"
            # 等价判定: 缓存均值是否落在无缓存 [min,max] 范围内 (±0 容忍, 范围即自然抖动)
            if not nc or not c:  # 全 null (cold-start preference_match) → 一致
                verdict = "✓(null)" if (not nc and not c) else "⚠null不一致"
            else:
                cmean = sum(c) / len(c)
                lo, hi = min(nc), max(nc)
                # 缓存均值落在无缓存范围, 或差距≤0.5 (1档内) → 视为无额外漂移
                inrange = (lo <= cmean <= hi) or (abs(cmean - sum(nc)/len(nc)) <= 0.5)
                verdict = "✓" if inrange else "✗漂移"
                if not inrange:
                    drift_flags.append(k)
            print(f"  {k:18}{nc_str:>16}{c_str:>14}{verdict:>8}")
        # safety 一致 = 两边出现的违规类型集合相同 (不按位置比, 避免 parse 失败导致的长度差假象)
        verdicts.append((case_id, drift_flags, set(nc_viol) == set(c_viol)))
        print()

    client.caches.delete(name=cache.name)
    print("=" * 72)
    n_drift = sum(len(d) for _, d, _ in verdicts)
    safety_match = all(s for _, _, s in verdicts)
    print(f"结论: 漂移维度数={n_drift} (0=缓存对打分透明) | safety 一致={safety_match}")
    if n_drift == 0 and safety_match:
        print("→ 缓存 vs 无缓存 分数无额外漂移, 在自然抖动范围内. 缓存方法上干净, 可用于全量.")
    else:
        print("→ 有漂移, 需进一步看是采样噪声还是缓存效应 (加大 REPS 复验).")


if __name__ == "__main__":
    main()
