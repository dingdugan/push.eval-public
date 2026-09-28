"""W3: Doubao 显式 Context API 缓存验证 (前缀缓存).

自动前缀缓存对 base64 内联视频不命中 (doubao_cache_probe 已验 cached=0). 火山有显式
Context API (common_prefix 模式): 把 system+视频 建成缓存前缀 → 拿 context_id → 每条
judge 调用只传变量文本, 视频走缓存. 这是 Doubao 版的 gemini CachedContent.

验: 建 context → 3 条 judge 调用 → 看 usage cached_tokens 是否 ~视频 token. 命中 → 全量
doubao 侧按缓存价 (cached 0.12元/M vs 0.6), 配合 gemini 缓存全量 judge ~$430.

CLI: PYTHONPATH=src .venv/bin/python src/doubao_context_probe.py
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from dotenv import load_dotenv
from volcenginesdkarkruntime import Ark

import judge_prompt as JP
from judge_probe import load_cases, load_output, parse_judge_json, validate_shape
from run_generation import render_user_prompt

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

MODEL = "doubao-seed-2-0-lite-260428"
VIDEO_DIR = Path("data/videos")
RMB = 7.2
# >32k tier ×1.5: 普通 in 0.9, cached in 0.18, out 5.4 元/M
P_IN, P_CACHED, P_OUT = 0.9, 0.18, 5.4


def _cached(usage) -> int:
    det = getattr(usage, "prompt_tokens_details", None)
    if det is None:
        return 0
    return (det.get("cached_tokens", 0) if isinstance(det, dict) else getattr(det, "cached_tokens", 0)) or 0


def main():
    client = Ark(api_key=os.environ["DOUBAO_API_KEY"],
                 base_url="https://ark.cn-beijing.volces.com/api/v3")
    cases = load_cases()
    yt = cases["V01_P1"]["video"]["youtube_id"]
    b64 = base64.b64encode((VIDEO_DIR / f"{yt}.mp4").read_bytes()).decode()
    vid_part = {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}}

    print("=" * 72)
    print(f"DOUBAO 显式 Context API 缓存验证 — {MODEL}, common_prefix(system+视频)")
    print("=" * 72)

    # 1. 建 context: system + 视频 作 common_prefix 缓存
    print("\n[1] context.create (system + 视频) mode=common_prefix ...")
    ctx = client.context.create(
        model=MODEL,
        mode="common_prefix",
        messages=[{"role": "system", "content": JP.JUDGE_SYSTEM},
                  {"role": "user", "content": [vid_part]}],
        ttl=3600,
    )
    print(f"    context_id={ctx.id}  缓存 prefix token={ctx.usage.prompt_tokens if ctx.usage else '?'}")

    # 2. 每条 judge 只传变量文本, 引用 context_id
    picks = [("V01_P1", "gpt-5.5"), ("V01_P5", "deepseek-v4-pro"), ("V01_P8", "gpt-5.5")]
    print("\n[2] context.completions.create (只传变量文本, 视频走缓存) ...")
    for i, (cid, gm) in enumerate(picks, 1):
        case = cases[cid]
        out = load_output(cid, gm)
        is_cold = (case["persona"]["lifecycle"] == "cold-start") or not case["persona"]["content_preference"]["categories"]
        ut = JP.build_judge_user_prompt(case["persona"], render_user_prompt(case, "A"),
                                        out["output_title"], out["output_body"])
        resp = client.context.completions.create(
            context_id=ctx.id, model=MODEL,
            messages=[{"role": "user", "content": ut}], max_tokens=8000)
        u = resp.usage
        tin, tout = u.prompt_tokens, u.completion_tokens
        cached = _cached(u)
        obj, ok = parse_judge_json(resp.choices[0].message.content or "")
        errs = validate_shape(obj, is_cold) if ok else ["parse FAIL"]
        billable = tin - cached
        c_cache = (billable * P_IN + cached * P_CACHED + tout * P_OUT) / 1e6 / RMB
        c_noc = (tin * P_IN + tout * P_OUT) / 1e6 / RMB
        print(f"\n  [{i}] {cid} | parse={'OK' if ok else 'FAIL'} | {'结构✓' if not errs else '结构✗'}")
        print(f"    prompt_token={tin}  cached={cached}  非缓存={billable}  out={tout}")
        print(f"    缓存计费 ${c_cache:.5f}  vs  无缓存 ${c_noc:.5f}"
              + (f"  → 省 {(1-c_cache/c_noc)*100:.0f}%" if cached else "  (未命中)"))

    print("\n" + "=" * 72)
    print("cached>0 = 显式 Context API 缓存生效 → 全量 doubao 走缓存价, judge ~$430.")


if __name__ == "__main__":
    main()
