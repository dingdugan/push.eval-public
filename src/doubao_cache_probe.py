"""W3: Doubao 视频缓存验证 — 全量 judge 是 ~$694(无缓存) 还是 ~$430(缓存) 的关键.

火山方舟有 cache 价 (0.12 元/M cached vs 0.6 普通 = 5x 便宜). 但「doubao 视频缓存能不能用」
没验过. 测自动前缀缓存: 同一视频(放消息最前作前缀)连发 2 次, 看第 2 次 usage 是否报
cached_tokens > 0. 命中 → 视频 token 走 cached 价, 全量大降.

视频放 message 最前(prefix), 文本在后(变量) → 前缀(系统+视频)相同, 第 2 次应命中.
顺序发(非并发)让第 2 次能吃到第 1 次的缓存.

CLI: PYTHONPATH=src .venv/bin/python src/doubao_cache_probe.py
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import judge_prompt as JP
from judge_probe import load_cases, load_output, parse_judge_json
from run_generation import render_user_prompt

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seed-2-0-lite-260428"
VIDEO_DIR = Path("data/videos")
# >32k tier ×1.5: 普通 in 0.9 元/M, cached in 0.12×1.5=0.18 元/M, out 5.4 元/M (RMB→USD ~7.2)
RMB = 7.2
P_IN, P_CACHED, P_OUT = 0.9, 0.18, 5.4


def _cached_tokens(usage) -> int:
    """从 usage 抠 cached prompt tokens (OpenAI 标准 prompt_tokens_details.cached_tokens; Ark 可能填)."""
    det = getattr(usage, "prompt_tokens_details", None)
    if det is None:
        return 0
    if isinstance(det, dict):
        return det.get("cached_tokens", 0) or 0
    return getattr(det, "cached_tokens", 0) or 0


def main():
    client = OpenAI(api_key=os.environ["DOUBAO_API_KEY"], base_url=BASE_URL)
    cases = load_cases()
    yt = cases["V01_P1"]["video"]["youtube_id"]
    b64 = base64.b64encode((VIDEO_DIR / f"{yt}.mp4").read_bytes()).decode()
    vid_part = {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}}

    print("=" * 72)
    print(f"DOUBAO 视频缓存验证 — {MODEL}, 同视频连发 2 次看第 2 次 cached_tokens")
    print("=" * 72)

    picks = [("V01_P1", "gpt-5.5"), ("V01_P5", "deepseek-v4-pro"), ("V01_P8", "gpt-5.5")]
    for i, (cid, gm) in enumerate(picks, 1):
        case = cases[cid]
        out = load_output(cid, gm)
        ut = JP.build_judge_user_prompt(case["persona"], render_user_prompt(case, "A"),
                                        out["output_title"], out["output_body"])
        # 视频放最前(前缀), 文本在后(变量)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": JP.JUDGE_SYSTEM},
                      {"role": "user", "content": [vid_part, {"type": "text", "text": ut}]}],
            max_tokens=8000,
        )
        u = resp.usage
        tin, tout = u.prompt_tokens, u.completion_tokens
        cached = _cached_tokens(u)
        obj, ok = parse_judge_json(resp.choices[0].message.content or "")
        billable = tin - cached
        cost = (billable * P_IN + cached * P_CACHED + tout * P_OUT) / 1e6 / RMB
        cost_nocache = (tin * P_IN + tout * P_OUT) / 1e6 / RMB
        print(f"\n[{i}] {cid} | parse={'OK' if ok else 'FAIL'}")
        print(f"    prompt_token={tin}  cached={cached}  非缓存={billable}  out={tout}")
        print(f"    缓存计费 ${cost:.5f}  vs  无缓存 ${cost_nocache:.5f}"
              + (f"  → 省 {(1-cost/cost_nocache)*100:.0f}%" if cached else "  (无缓存命中)"))

    print("\n" + "=" * 72)
    print("看第 2、3 次 cached>0 = 自动前缀缓存生效, 视频 token 走 cached 价, 全量按 ~$430 算.")
    print("若全程 cached=0 = 自动缓存没生效 (可能需显式 Context API 或 Ark 控制台开启), 按无缓存 ~$694.")


if __name__ == "__main__":
    main()
