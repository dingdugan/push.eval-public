"""W3 probe 第 4 点续: Gemini 视频 context 缓存 — 全量成本能否压回 design-doc 量级.

无缓存实测: gemini judge 单条 ~$0.0235, 其中视频 ~58K input token 占绝大部分.
一个视频被 judge 评很多次 (该视频所有 output × judge 数). 若每次重传视频 token,
全量 ~$1100 (远超 doc $470). doc 的 $470 假设了视频缓存 —— 这里实测能省多少.

Gemini 显式缓存 (CachedContent): 把视频 + system_instruction 建成 cache → 拿 cache name →
后续 generate_content 引用 cache, 视频 token 走 cached 计价 (大幅折扣) + 每小时存储费.

验: 建 cache → 同视频跑 2 条 judge → 看 usage_metadata 里 cached_content_token_count
是不是 ~58K, 实际计费 input 是不是只剩文本部分. 对比无缓存单条成本.

CLI: PYTHONPATH=src .venv/bin/python src/judge_cache_probe.py
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

import judge_prompt as JP
from run_generation import render_user_prompt

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

VIDEO_DIR = Path("data/videos")
TEST_CASES = Path("data/test_cases.jsonl")
VAR_A = Path("results/var_A_raw.jsonl")
MODEL = "gemini-2.5-flash"
# 缓存计价 (per 1M token). gemini-2.5-flash: 普通 input $0.30; cached input 折扣 + 存储费/小时.
PRICE_IN, PRICE_OUT = 0.30, 2.50
PRICE_CACHED_IN = 0.075       # cached input 通常 ~普通 1/4 (官方 2026 pricing; 实测 token 数为准对账)
PRICE_CACHE_STORAGE = 1.00    # 存储 per 1M token-hour (量级; 缓存只活几分钟可忽略)


def load_cases() -> dict:
    return {json.loads(l)["case_id"]: json.loads(l) for l in TEST_CASES.open(encoding="utf-8")}


def load_output(case_id: str, model: str) -> dict:
    for l in VAR_A.open(encoding="utf-8"):
        d = json.loads(l)
        if d["case_id"] == case_id and d["model"] == model and d["run_id"] == 1:
            return d
    raise LookupError(f"{case_id}|{model}")


def main():
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cases = load_cases()
    youtube_id = cases["V01_P1"]["video"]["youtube_id"]
    path = VIDEO_DIR / f"{youtube_id}.mp4"

    print("=" * 72)
    print(f"GEMINI 视频 CONTEXT 缓存 PROBE — {path.name} ({path.stat().st_size/1e6:.1f}MB)")
    print("=" * 72)

    # 1. 上传视频
    print("\n[1] Files API 上传视频 ...")
    f = client.files.upload(file=str(path))
    for _ in range(60):
        f = client.files.get(name=f.name)
        if f.state == "ACTIVE":
            break
        if f.state == "FAILED":
            raise RuntimeError("文件 FAILED")
        time.sleep(2)
    print(f"    ACTIVE: {f.name}")

    # 2. 建 cache (视频 + system_instruction). judge 的 user 文本每条不同 → 不进 cache.
    print("\n[2] 建 CachedContent (视频 + JUDGE_SYSTEM) ...")
    try:
        cache = client.caches.create(
            model=MODEL,
            config=types.CreateCachedContentConfig(
                contents=[f],
                system_instruction=JP.JUDGE_SYSTEM,
                ttl="600s",  # 10 分钟够 probe; 全量按视频批处理时延长
            ),
        )
    except Exception as ex:
        print(f"    [缓存创建失败] {type(ex).__name__}: {str(ex)[:300]}")
        print("    → 视频缓存不可用 (或 token 数不达最小缓存阈值). 全量成本按无缓存 ~$1100 量级.")
        return
    cached_tok = getattr(cache.usage_metadata, "total_token_count", None) if cache.usage_metadata else None
    print(f"    cache name: {cache.name}")
    print(f"    缓存 token 数: {cached_tok}")

    # 3. 同视频跑 2 条 judge (引用 cache), 看 cached_content_token_count + 实际计费 input
    cfg = types.GenerateContentConfig(
        cached_content=cache.name,
        max_output_tokens=8000,
        response_mime_type="application/json",
    )
    picks = [("V01_P1", "gpt-5.5"), ("V01_P5", "deepseek-v4-pro")]
    print("\n[3] 引用 cache 跑 judge (视频 token 应走 cached) ...")
    for case_id, gen_model in picks:
        case = cases[case_id]
        out = load_output(case_id, gen_model)
        var_input = render_user_prompt(case, "A")
        user_text = JP.build_judge_user_prompt(
            case["persona"], var_input, out["output_title"], out["output_body"])
        from google.genai import errors as genai_errors
        t0 = time.time()
        for attempt in range(5):  # 503 高需求退避
            try:
                resp = client.models.generate_content(model=MODEL, contents=[user_text], config=cfg)
                break
            except genai_errors.ServerError:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt * 3)
        dt = time.time() - t0
        um = resp.usage_metadata
        tin = um.prompt_token_count or 0
        cached = getattr(um, "cached_content_token_count", None) or 0
        thoughts = getattr(um, "thoughts_token_count", None) or 0
        cand = um.candidates_token_count or 0
        tout = cand + thoughts
        billable_in = tin - cached  # 非缓存的 input (文本 prompt)
        # 计费: 非缓存 input @0.30 + cached input @0.075 + output @2.50
        c_cached = (billable_in * PRICE_IN + cached * PRICE_CACHED_IN + tout * PRICE_OUT) / 1e6
        c_nocache = (tin * PRICE_IN + tout * PRICE_OUT) / 1e6
        ok = bool(re.search(r"\{.*\}", resp.text or "", flags=re.S))
        print(f"\n  [{case_id}] {dt:.1f}s | parse={'OK' if ok else 'FAIL'}")
        print(f"    prompt_token={tin}  其中 cached={cached}  非缓存(文本)={billable_in}  out={tout}")
        print(f"    缓存计费 ${c_cached:.5f}  vs  无缓存 ${c_nocache:.5f}  → 省 {(1-c_cached/c_nocache)*100:.0f}%")

    # 4. 清理 cache
    print("\n[4] 删除 cache ...")
    try:
        client.caches.delete(name=cache.name)
        print("    已删")
    except Exception as ex:
        print(f"    删除失败 (TTL 到期会自动清): {str(ex)[:100]}")

    print("\n" + "=" * 72)
    print("结论: 若 cached>0 且省 70%+ → 全量按缓存计价可压回 design-doc 量级.")
    print("全量该视频被评 N 次: 视频 token 只在建 cache 时算一次 (+存储费), N 条 judge 只付文本 input.")


if __name__ == "__main__":
    main()
