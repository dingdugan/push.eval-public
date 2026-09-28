"""W3 第一动作: judge reality-probe.

目的 (retro 头号教训: 别纸上设计先跑全量). 全量 judge 是 W3 最大开销 (~$470+).
先花几美分验 4 件事:
  1. 完整视频能不能传进 judge 模型 (gemini-2.5-flash / kimi-k2.6) 拿到合法响应
     —— 我们连 var D 都没跑过, 从没给这些模型传过视频, 这是最大未知.
  2. judge prompt 跑通: 输出是不是合法 JSON (safety 4 + effect 9)?
  3. preference_match 在 cold-start persona 是不是 null?
  4. 量单条成本 (输入含完整视频, 大) → 外推全量, 跟 design-doc ~$470 对账.

只用单视频 V01 (aRNfSqsgrgE.mp4, 22MB) × 3 条真实 output × 2 judge = 6 次 judge 调用.
被评 output 故意选非 judge 模型 (gpt-5.5 / deepseek), 避免 probe 阶段混入自评.

Gemini 视频: Files API 上传 (22MB > 20MB inline 上限) → 等 ACTIVE → generate_content.
Kimi 视频: Moonshot OpenAI 兼容, 是否支持视频未知 —— probe 就是要实测; 失败=真发现.

CLI: PYTHONPATH=src .venv/bin/python src/judge_probe.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

import judge_prompt as JP
from run_generation import render_user_prompt  # var A 的 generator 输入文本 (作 judge context)

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

VIDEO_DIR = Path("data/videos")
TEST_CASES = Path("data/test_cases.jsonl")
VAR_A = Path("results/var_A_raw.jsonl")

# judge 输入视频价 (per 1M token, design-doc § 4.2 同表; gemini-2.5-flash 视频按 token 计)
PRICES = {"gemini-2.5-flash": (0.30, 2.50), "kimi-k2.6": (1.00, 3.00)}

# probe 挑的 3 条 (case_id, 被评 generator 模型) —— 全 V01, lifecycle 多样, 非 judge 模型
PROBE_PICKS = [
    ("V01_P1", "gpt-5.5"),        # cold-start  → 验 preference_match=null
    ("V01_P5", "deepseek-v4-pro"),# engaged     → engaged tone anchor + 有偏好
    ("V01_P8", "gpt-5.5"),        # at-risk     → at-risk tone anchor
]
JUDGES = ["gemini-2.5-flash", "kimi-k2.6"]


def load_cases() -> dict:
    return {json.loads(l)["case_id"]: json.loads(l) for l in TEST_CASES.open(encoding="utf-8")}


def load_output(case_id: str, model: str) -> dict:
    """从 var_A_raw 取该 (case, model) 的 run_id=1 output."""
    for l in VAR_A.open(encoding="utf-8"):
        d = json.loads(l)
        if d["case_id"] == case_id and d["model"] == model and d["run_id"] == 1:
            return d
    raise LookupError(f"{case_id}|{model} 在 var_A_raw 里没找到")


def parse_judge_json(text: str) -> tuple[dict | None, bool]:
    """抠 judge 输出的 JSON. 返回 (obj, ok).

    用 raw_decode 解「第一个完整对象」并忽略尾部多余 —— judge 偶尔在合法 JSON 后多吐
    字符 (实测 Doubao 会多一个 `}`; 贪婪正则 \\{.*\\} 会把多余括进去导致 Extra data 失败).
    """
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = t.find("{")
    if start < 0:
        return None, False
    try:
        obj, _ = json.JSONDecoder().raw_decode(t, start)  # 解第一个完整对象, 尾部多余忽略
        return obj, True
    except json.JSONDecodeError:
        return None, False


def validate_shape(obj: dict, is_cold: bool) -> list[str]:
    """检查 judge JSON 结构: safety 4 binary + effect 9 (1-5); cold-start preference_match=null."""
    errs = []
    safety_keys = {"misleading", "manipulation", "harmful", "privacy"}
    effect_keys = {"readability", "video_relevance", "faithfulness", "expressiveness",
                   "naturalness", "expectation_match", "preference_match", "tone_fit",
                   "interruption_value"}
    s = obj.get("safety", {})
    if set(s) != safety_keys:
        errs.append(f"safety 键不对: {set(s) ^ safety_keys}")
    for k, v in s.items():
        if not isinstance(v.get("violation"), bool):
            errs.append(f"safety.{k}.violation 非 bool")
    e = obj.get("effect", {})
    if set(e) != effect_keys:
        errs.append(f"effect 键不对: {set(e) ^ effect_keys}")
    for k, v in e.items():
        # judge 可能把维度塌缩成字面 null (尤其 cold-start 的 preference_match) 而非 {"score":null,...}
        sc = v.get("score") if isinstance(v, dict) else v
        if k == "preference_match":
            if is_cold and sc is not None:
                errs.append(f"cold-start 但 preference_match={sc} (应 null)")
            if not is_cold and not (isinstance(sc, (int, float)) and 1 <= sc <= 5):
                errs.append(f"preference_match={sc} 非 1-5")
        elif not (isinstance(sc, (int, float)) and 1 <= sc <= 5):
            errs.append(f"effect.{k}.score={sc} 非 1-5")
    return errs


# ─────────────────────────────────────────────────────────
# vendor: 传完整视频 + judge prompt → 原始响应 + token
# ─────────────────────────────────────────────────────────

_GEMINI_FILE_CACHE: dict[str, object] = {}  # youtube_id → uploaded file (同视频只传一次)


def judge_gemini(model: str, youtube_id: str, user_text: str) -> dict:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    if youtube_id not in _GEMINI_FILE_CACHE:
        path = VIDEO_DIR / f"{youtube_id}.mp4"
        print(f"    [gemini] 上传 {path.name} ({path.stat().st_size/1e6:.1f}MB) via Files API ...")
        f = client.files.upload(file=str(path))
        # 等转 ACTIVE (视频要处理)
        for _ in range(60):
            f = client.files.get(name=f.name)
            if f.state == "ACTIVE":
                break
            if f.state == "FAILED":
                raise RuntimeError(f"Gemini 文件处理 FAILED: {f.name}")
            time.sleep(2)
        else:
            raise RuntimeError("Gemini 文件 60×2s 仍未 ACTIVE")
        print(f"    [gemini] 文件 ACTIVE: {f.name}")
        _GEMINI_FILE_CACHE[youtube_id] = f
    vid = _GEMINI_FILE_CACHE[youtube_id]
    cfg = types.GenerateContentConfig(
        system_instruction=JP.JUDGE_SYSTEM,
        max_output_tokens=8000,  # 4000 实测被 thoughts 吃光→正文 JSON 截断 parse FAIL
        response_mime_type="application/json",
    )
    from google.genai import errors as genai_errors
    for attempt in range(5):  # 503 高需求瞬时过载 → 指数退避 (仿 run_generation.call_gemini)
        try:
            resp = client.models.generate_content(model=model, contents=[vid, user_text], config=cfg)
            break
        except genai_errors.ServerError:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)
    um = resp.usage_metadata
    tin = um.prompt_token_count or 0
    thoughts = getattr(um, "thoughts_token_count", None) or 0
    cand = um.candidates_token_count or 0
    return {"text": resp.text or "", "tokens_in": tin, "tokens_out": cand + thoughts}


def judge_kimi(model: str, youtube_id: str, user_text: str) -> dict:
    """Moonshot OpenAI 兼容. 视频支持未知 —— 试 video_url content type, 失败=真发现."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["MOONSHOT_API_KEY"], base_url="https://api.moonshot.cn/v1")
    path = VIDEO_DIR / f"{youtube_id}.mp4"
    b64 = base64.b64encode(path.read_bytes()).decode()
    user_content = [
        {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
        {"type": "text", "text": user_text},
    ]
    kw = {"model": model,
          "messages": [{"role": "system", "content": JP.JUDGE_SYSTEM},
                       {"role": "user", "content": user_content}],
          "extra_body": {"thinking": {"type": "disabled"}}}  # 跟 generation 一致关思考
    try:
        resp = client.chat.completions.create(max_completion_tokens=4000, **kw)
    except Exception:
        resp = client.chat.completions.create(max_tokens=4000, **kw)
    msg = resp.choices[0].message
    u = resp.usage
    return {"text": msg.content or "", "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens}


def run_judge(judge_model: str, youtube_id: str, user_text: str) -> dict:
    if judge_model.startswith("gemini"):
        return judge_gemini(judge_model, youtube_id, user_text)
    return judge_kimi(judge_model, youtube_id, user_text)


def cost(model: str, tin: int, tout: int) -> float:
    pi, po = PRICES[model]
    return (tin * pi + tout * po) / 1e6


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--judges", nargs="*", default=JUDGES, help="只跑这些 judge (默认两家)")
    args = ap.parse_args()
    judges = args.judges
    cases = load_cases()
    results = []  # (case, gen_model, judge, ok, errs, cost, tin, tout)
    print("=" * 72)
    print("JUDGE REALITY-PROBE — 3 output × 2 judge, 单视频 V01, 传完整视频")
    print("=" * 72)

    for case_id, gen_model in PROBE_PICKS:
        case = cases[case_id]
        persona = case["persona"]
        video = case["video"]
        youtube_id = video["youtube_id"]
        lifecycle = persona["lifecycle"]
        is_cold = (lifecycle == "cold-start") or not persona["content_preference"]["categories"]
        out = load_output(case_id, gen_model)
        var_input = render_user_prompt(case, "A")  # generator 当时看到的 (作 judge context)
        user_text = JP.build_judge_user_prompt(
            persona, var_input, out["output_title"], out["output_body"])

        print(f"\n{'─'*72}\n[{case_id}] gen={gen_model} | lifecycle={lifecycle} | cold={is_cold}")
        print(f"  被评 → T: {out['output_title']}")
        print(f"        B: {out['output_body']}")

        for judge in judges:
            print(f"\n  ── judge={judge} ──")
            try:
                t0 = time.time()
                r = run_judge(judge, youtube_id, user_text)
                dt = time.time() - t0
            except Exception as ex:
                print(f"    [ERROR] {type(ex).__name__}: {str(ex)[:200]}")
                results.append((case_id, gen_model, judge, False, [f"调用失败: {type(ex).__name__}"], 0, 0, 0))
                continue
            obj, parse_ok = parse_judge_json(r["text"])
            c = cost(judge, r["tokens_in"], r["tokens_out"])
            print(f"    {dt:.1f}s | tin={r['tokens_in']} tout={r['tokens_out']} | ${c:.5f} | parse={'OK' if parse_ok else 'FAIL'}")
            if not parse_ok:
                print(f"    [原文前 300]: {r['text'][:300]!r}")
                results.append((case_id, gen_model, judge, False, ["JSON parse 失败"], c, r["tokens_in"], r["tokens_out"]))
                continue
            errs = validate_shape(obj, is_cold)
            eff = obj.get("effect", {})
            scores = {k: (eff[k].get("score") if isinstance(eff[k], dict) else eff[k]) for k in eff}
            viol = [k for k, v in obj.get("safety", {}).items() if v.get("violation")]
            print(f"    safety 违规: {viol or '无'}")
            print(f"    effect: " + " ".join(f"{k[:4]}={scores[k]}" for k in scores))
            if errs:
                print(f"    [结构问题] {errs}")
            else:
                print(f"    [结构 ✓] safety4 + effect9 全合法; pref_match={scores.get('preference_match')} (cold={is_cold})")
            results.append((case_id, gen_model, judge, not errs, errs, c, r["tokens_in"], r["tokens_out"]))

    # ── 汇总 + 全量外推 ──
    print(f"\n{'='*72}\n汇总")
    ok = sum(1 for r in results if r[3])
    print(f"  合法响应: {ok}/{len(results)}")
    by_judge = {}
    for cid, gm, j, good, errs, c, ti, to in results:
        by_judge.setdefault(j, []).append((c, ti, to))
    # design-doc: 全量 judge 量级. 4992(A)+3744(B)+var C/D + 2 judge. 这里只验单条→外推一个量级.
    print(f"\n  {'judge':18}{'n':>3}{'avg_tin':>9}{'avg_tout':>9}{'$/call':>9}")
    for j, rs in by_judge.items():
        good = [x for x in rs if x[0] > 0]
        if not good:
            print(f"  {j:18}{len(rs):>3}   (全失败)")
            continue
        ati = sum(x[1] for x in good) / len(good)
        ato = sum(x[2] for x in good) / len(good)
        pc = sum(x[0] for x in good) / len(good)
        print(f"  {j:18}{len(good):>3}{ati:>9.0f}{ato:>9.0f}{pc:>9.5f}")
    print("\n  注: 单条 $/call × 全量 judge 调用数 = 全量开销; 全量量级 W3 写 runner 时按 var 覆盖矩阵定.")
    print("  视频缓存: 同视频 Gemini Files API 只传一次 (32 视频, 复用); batch / 缓存可进一步降本.")


if __name__ == "__main__":
    main()
