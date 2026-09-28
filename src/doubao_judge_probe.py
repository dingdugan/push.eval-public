"""W3: Doubao judge reality-probe (第 2 步) — 验 Doubao Seed 2.0 lite 能否当 video judge.

第 1 步已验: key 有效 + doubao-seed-2-0-lite 文本 auth 通.
本步验: (1) 完整视频能否传进 Doubao (base64 内联 video_url, 仿 kimi);
        (2) judge prompt 输出是否合法 13 维 JSON (safety4+effect9);
        (3) cold-start preference_match=null; (4) 量成本.

通过 → judge 池第 3 家就位, 全量 judge 配对+成本可定死.
CLI: PYTHONPATH=src .venv/bin/python src/doubao_judge_probe.py
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import judge_prompt as JP
from judge_probe import load_cases, load_output, parse_judge_json, validate_shape
from run_generation import render_user_prompt

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seed-2-0-lite-260428"
VIDEO_DIR = Path("data/videos")
# Doubao Seed 2.0 lite 价 [TBD: 开通后控制台/官方 pricing 核实; 先按实测 token 记成本结构, 单价待填]
PRICE_IN, PRICE_OUT = None, None  # 不编单价; 只报 token, 成本待 verify 单价后算
PICKS = [("V01_P1", "gpt-5.5"), ("V01_P5", "deepseek-v4-pro")]

# 视频传给 Doubao 的方式 (env 切换):
#   url (默认): 传 TOS 公网 URL, Doubao 服务端拉. 绕过两个硬伤 —
#       (1) 内联体积上限 (~base64 70MB → 最大 3 个视频 413 Request Entity Too Large);
#       (2) 每条评都重传 (16185 条 × 中位 22MB ≈ 356GB 重复上传).
#       前置: 先跑 upload_tos.py 把 32 个视频传到 TOS (key=<youtube_id>.mp4). 公司网络下 TOS 上传快 + Doubao 北京内网拉快.
#   inline: base64 内联 (家用网络跑不动 + 大视频 413; 仅留作回退 / 对照, 用 DOUBAO_VIDEO_MODE=inline 切).
TOS_PUBLIC_BASE = os.environ.get("TOS_PUBLIC_BASE", "https://push-eval-videos.tos-cn-beijing.volces.com")
DOUBAO_VIDEO_MODE = os.environ.get("DOUBAO_VIDEO_MODE", "url")


def doubao_video_url(youtube_id: str) -> str:
    """Doubao video_url 用的视频地址: url=TOS 公网 URL (默认), inline=base64 data URI (回退)."""
    if DOUBAO_VIDEO_MODE == "inline":
        b64 = base64.b64encode((VIDEO_DIR / f"{youtube_id}.mp4").read_bytes()).decode()
        return f"data:video/mp4;base64,{b64}"
    return f"{TOS_PUBLIC_BASE}/{youtube_id}.mp4"


def judge_doubao(client, youtube_id: str, user_text: str) -> dict:
    user_content = [
        {"type": "video_url", "video_url": {"url": doubao_video_url(youtube_id)}},
        {"type": "text", "text": user_text},
    ]
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": JP.JUDGE_SYSTEM},
                  {"role": "user", "content": user_content}],
        max_tokens=8000,  # 4000 实测被 thinking/13维why 吃光→JSON 截断 parse FAIL (同 gemini)
    )
    msg = resp.choices[0].message
    u = resp.usage
    return {"text": msg.content or "", "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens}


def main():
    client = OpenAI(api_key=os.environ["DOUBAO_API_KEY"], base_url=BASE_URL)
    cases = load_cases()
    print("=" * 72)
    print(f"DOUBAO judge reality-probe — {MODEL}, 完整视频 V01, 2 output")
    print("=" * 72)
    for case_id, gen_model in PICKS:
        case = cases[case_id]
        persona = case["persona"]
        youtube_id = case["video"]["youtube_id"]
        lifecycle = persona["lifecycle"]
        is_cold = (lifecycle == "cold-start") or not persona["content_preference"]["categories"]
        out = load_output(case_id, gen_model)
        var_input = render_user_prompt(case, "A")
        user_text = JP.build_judge_user_prompt(persona, var_input, out["output_title"], out["output_body"])
        print(f"\n── [{case_id}] gen={gen_model} | lifecycle={lifecycle} | cold={is_cold} ──")
        try:
            t0 = time.time()
            r = judge_doubao(client, youtube_id, user_text)
            dt = time.time() - t0
        except Exception as ex:
            print(f"  [ERROR] {type(ex).__name__}: {str(ex)[:260]}")
            continue
        obj, ok = parse_judge_json(r["text"])
        print(f"  {dt:.1f}s | tin={r['tokens_in']} tout={r['tokens_out']} | parse={'OK' if ok else 'FAIL'}")
        if not ok:
            print(f"  [原文前 300] {r['text'][:300]!r}")
            continue
        errs = validate_shape(obj, is_cold)
        eff = obj.get("effect", {})
        scores = {k: (eff[k].get("score") if isinstance(eff[k], dict) else eff[k]) for k in eff}
        viol = [k for k, v in obj.get("safety", {}).items() if isinstance(v, dict) and v.get("violation")]
        print(f"  safety 违规: {viol or '无'}")
        print(f"  effect: " + " ".join(f"{k[:4]}={scores[k]}" for k in scores))
        print(f"  {'[结构 ✓]' if not errs else '[结构问题] '+str(errs)} pref_match={scores.get('preference_match')} (cold={is_cold})")


if __name__ == "__main__":
    main()
