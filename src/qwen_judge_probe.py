"""W3: Qwen-VL judge probe — 验阿里 Qwen-VL 能否当副评 (DashScope).

副评候选: Qwen-VL (阿里, 不是生成模型 → 能评所有 output, 跨厂独立). 单价比 Kimi 便宜.
验: (1) 文本 auth; (2) 完整视频能传进 (base64 内联 video_url); (3) 合法 13 维 JSON +
cold-start null; (4) 实测视频 token 数 → 真实 per-call 成本 (之前估算的水分在这).

DashScope OpenAI 兼容: base https://dashscope.aliyuncs.com/compatible-mode/v1.
CLI: PYTHONPATH=src .venv/bin/python src/qwen_judge_probe.py
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

BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"  # key 为国际站 (国内站 401)
VIDEO_DIR = Path("data/videos")
# 单价 (USD/M, web 查): qwen-vl-max $0.52/$2.08; qwen3-vl-plus 更便宜档
PRICES = {"qwen-vl-max": (0.52, 2.08), "qwen3-vl-plus": (0.21, 1.05)}
PICKS = [("V01_P1", "gpt-5.5"), ("V01_P5", "deepseek-v4-pro")]


def judge_qwen(client, model, youtube_id, user_text):
    b64 = base64.b64encode((VIDEO_DIR / f"{youtube_id}.mp4").read_bytes()).decode()
    uc = [{"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
          {"type": "text", "text": user_text}]
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": JP.JUDGE_SYSTEM},
                  {"role": "user", "content": uc}],
        max_tokens=8000)
    u = resp.usage
    return {"text": resp.choices[0].message.content or "", "tin": u.prompt_tokens, "tout": u.completion_tokens}


def main():
    client = OpenAI(api_key=os.environ["DASHSCOPE_API_KEY"], base_url=BASE_URL)
    cases = load_cases()
    print("=" * 72)
    print("QWEN-VL judge probe (DashScope) — 完整视频 V01")
    print("=" * 72)

    for model in ["qwen-vl-max", "qwen3-vl-plus"]:
        pi, po = PRICES[model]
        print(f"\n{'='*60}\n模型: {model}  (${pi}/${po} per M)\n{'='*60}")
        # 文本 auth
        try:
            r = client.chat.completions.create(model=model,
                messages=[{"role": "user", "content": "Reply exactly: OK"}], max_tokens=2000)
            print(f"  [文本 auth] ✓ {r.choices[0].message.content!r}")
        except Exception as ex:
            print(f"  [文本 auth] ✗ {type(ex).__name__}: {str(ex)[:200]}")
            continue
        # 视频 judge
        for cid, gm in PICKS:
            case = cases[cid]
            is_cold = (case["persona"]["lifecycle"] == "cold-start") or not case["persona"]["content_preference"]["categories"]
            out = load_output(cid, gm)
            ut = JP.build_judge_user_prompt(case["persona"], render_user_prompt(case, "A"),
                                            out["output_title"], out["output_body"])
            try:
                t0 = time.time()
                r = judge_qwen(client, model, case["video"]["youtube_id"], ut)
                dt = time.time() - t0
            except Exception as ex:
                print(f"  [{cid}] ✗ {type(ex).__name__}: {str(ex)[:200]}")
                continue
            obj, ok = parse_judge_json(r["text"])
            errs = validate_shape(obj, is_cold) if ok else ["parse FAIL"]
            cost = (r["tin"] * pi + r["tout"] * po) / 1e6
            scores = {}
            if ok:
                eff = obj.get("effect", {})
                scores = {k: (eff[k].get("score") if isinstance(eff[k], dict) else eff[k]) for k in eff}
            print(f"  [{cid}] {dt:.0f}s | tin={r['tin']} (视频token) tout={r['tout']} | ${cost:.5f} | "
                  f"parse={'OK' if ok else 'FAIL'} | {'结构✓' if not errs else '结构✗'+str(errs)[:90]}")
            if ok and not errs:
                print(f"        pref_match={scores.get('preference_match')} (cold={is_cold})")


if __name__ == "__main__":
    main()
