"""Day 11-12: var A (metadata-only) generator. 4 模型 × test_case × 3 重复 → OutputRow.

本文件先支持 pilot (少量 case 观测成本+输入输出+坑), 全量跑后续接.

各 vendor 调用形态不同 (W2 实测):
  - Gemini: google-genai SDK, system_instruction + GenerateContentConfig; usage_metadata
  - GPT-5.5 / DeepSeek / Kimi: OpenAI 兼容 (不同 base_url + model); usage.completion_tokens 含 reasoning
  - 全部 reasoning 模型: 不设 temperature (用默认; gpt/kimi 锁死, 见 design-doc § 4.2)
  - max token 给足 (2000): reasoning 占额度, 太小会截断正文

CLI:
  python src/run_generation.py pilot                 # 4 模型 × PILOT_N case, 打印观测
  python src/run_generation.py pilot --n 3 --models gemini-3.5-flash
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from schemas import OutputRow, PreprocessMeta, VarLabel

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

TEST_CASES = Path("data/test_cases.jsonl")

# 价格 (input/output 美金 per 1M token, design-doc § 4.2)
PRICES = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-2.5-flash": (0.30, 2.50),  # 3.5-flash 发布期持续 503, user 决策降级 (官方 pricing 2026)
    "gpt-5.5": (5.00, 30.00),
    "kimi-k2.6": (1.00, 3.00),
    "deepseek-v4-pro": (0.145, 3.48),
}
OPENAI_COMPAT = {  # model → (env_key, base_url)
    "gpt-5.5": ("OPENAI_API_KEY", None),
    "deepseek-v4-pro": ("DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    "kimi-k2.6": ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1"),
}
MAX_TOKENS = 8000  # 给足: reasoning 模型(Kimi/DeepSeek)思考吃额度, 2000 实测被 Kimi 思考烧光→正文空

STYLE_EN = {"未知": "unknown", "单偏好": "single-focused",
            "窄偏好": "a few related interests", "宽偏好": "broad/varied interests"}

SYSTEM_PROMPT = (
    "You are a push-notification copywriter for a short-video app. "
    "Given one video and one target user, write a single push notification (title + body) "
    "that makes THIS user want to open and watch the video.\n"
    "Rules:\n"
    "- Output ONLY a JSON object: {\"title\": \"...\", \"body\": \"...\"}\n"
    "- Hard limits: title ≤ 50 characters, body ≤ 150 characters.\n"
    "- Personalize to the user's lifecycle stage and content interests.\n"
    "- Be faithful to the video; do not mislead or over-promise beyond its actual content "
    "(misleading copy that doesn't match the video erodes user trust).\n"
    "- The user does NOT follow or know this creator — no social hook available.\n"
    "- Write in English."
)


KEYFRAME_DIR = Path("data/preprocess/keyframes")
KEYFRAME_CAP = 5  # 每视频最多传几帧 (跟预处理 cap 一致)
TRANSCRIPT_DIR = Path("data/preprocess/transcripts")
VIDEO_DIR = Path("data/videos")  # var D: 原生视频 {youtube_id}.mp4

# var D 仅这两家支持视频 (实测: gpt/deepseek 纯文本看不了视频, judge 能力矩阵已验)
VIDEO_MODELS = {"gemini-2.5-flash", "gemini-3.5-flash", "kimi-k2.6"}

# Gemini Files API: 同视频只传一次, 跨 worker 复用 (32 视频 × 39 次调用/视频).
# workers=8 并发 → 加锁防同视频被重复上传 + 竞态. 上传在锁内串行 (一次性, 可接受).
_GEMINI_FILE_CACHE: dict[str, object] = {}  # youtube_id → uploaded file
_GEMINI_FILE_LOCK = threading.Lock()


def load_keyframes(video_id: str) -> list[bytes]:
    """读 data/preprocess/keyframes/{video_id}/frame_*.jpg → bytes 列表 (按 frame 序)."""
    d = KEYFRAME_DIR / video_id
    frames = sorted(d.glob("frame_*.jpg"), key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)))
    return [f.read_bytes() for f in frames[:KEYFRAME_CAP]]


def load_transcript(video_id: str) -> dict:
    """读 data/preprocess/transcripts/{video_id}.json (Whisper 转写: text + segments + language)."""
    return json.loads((TRANSCRIPT_DIR / f"{video_id}.json").read_text(encoding="utf-8"))


def transcript_meta(video_id: str) -> PreprocessMeta:
    """var C 行的 preprocess_meta: 语种 / 词数 / 置信度 (segments avg_logprob 均值)."""
    tr = load_transcript(video_id)
    segs = tr.get("segments") or []
    conf = sum(s.get("avg_logprob", 0.0) for s in segs) / len(segs) if segs else None
    return PreprocessMeta(
        transcript_language=tr.get("language"),
        transcript_word_count=len(tr.get("text", "").split()),
        transcript_confidence=round(conf, 4) if conf is not None else None,
    )


def render_user_prompt(case: dict, var: str = "A") -> str:
    """var A: metadata only. var B: metadata + 关键帧图像提示 (图像另按 vendor 格式附加).
    var C: metadata + 音轨转写文本 (纯文本嵌入 prompt, 4 模型含 DeepSeek 都能吃)."""
    p = case["persona"]
    v = case["video"]
    cp = p["content_preference"]
    style = STYLE_EN.get(cp["style"], cp["style"])
    cats = ", ".join(cp["categories"]) if cp["categories"] else "(unknown — new user)"
    comments = v.get("top_comments") or []
    comments_str = "\n".join(f"  - {c[:120]}" for c in comments[:5]) or "  (none)"
    desc = (v.get("video_description") or "")[:500]
    tags = ", ".join((v.get("video_tags") or [])[:10]) or "(none)"
    video_header = {
        "A": "[Video — metadata only]",
        "B": "[Video — metadata + keyframe images attached]",
        "C": "[Video — metadata + full audio transcript]",
        "D": "[Video — full native video attached]",
    }.get(var, "[Video — metadata only]")
    closing = {
        "A": "Write the push notification now (JSON only).",
        "B": "The attached images are keyframes sampled from the video — use them to "
             "ground the copy in what is actually shown. Write the push notification now (JSON only).",
        "C": "The transcript below is the video's spoken audio (auto-transcribed; may be noisy, "
             "partial, or non-English) — use it to ground the copy in what is actually said. "
             "Write the push notification now (JSON only).",
        "D": "The full video is attached — watch it and ground the copy in what is actually "
             "shown and said. Write the push notification now (JSON only).",
    }.get(var, "Write the push notification now (JSON only).")
    # var C: 音轨转写文本嵌入 (整段, 最长 ~7k 字符 ≈ 1.7k token, 无需截断)
    transcript_block = ""
    if var == "C":
        tr = load_transcript(v["video_id"])
        txt = (tr.get("text") or "").strip() or "(transcript empty — little or no speech in audio)"
        transcript_block = f"\n[Audio transcript]\n{txt}\n"
    return (
        "[Push context] The system is recommending a video by a creator the user does NOT follow.\n\n"
        "[Target user]\n"
        f"- Lifecycle stage: {p['lifecycle']}\n"
        f"- Content preference: {style}\n"
        f"- Interested categories: {cats}\n"
        f"- Last active: {p['last_active']}\n\n"
        f"{video_header}\n"
        f"- Category: {v['category']}\n"
        f"- Title: {v['video_title']}\n"
        f"- Channel: {v['channel']}\n"
        f"- Description: {desc}\n"
        f"- Tags: {tags}\n"
        f"- Top comments:\n{comments_str}\n"
        f"{transcript_block}\n"
        f"{closing}"
    )


# ─────────────────────────────────────────────────────────
# vendor 调用 → (text, tokens_in, tokens_out, reasoning_tokens, model_version)
# ─────────────────────────────────────────────────────────

def _get_gemini_video(client, youtube_id: str):
    """var D: Files API 上传视频, 等 ACTIVE, 跨 worker 复用 (加锁). 复用 judge_probe 验通的流程."""
    with _GEMINI_FILE_LOCK:
        if youtube_id in _GEMINI_FILE_CACHE:
            return _GEMINI_FILE_CACHE[youtube_id]
        path = VIDEO_DIR / f"{youtube_id}.mp4"
        print(f"  [gemini] 上传 {path.name} ({path.stat().st_size/1e6:.1f}MB) via Files API ...")
        f = client.files.upload(file=str(path))
        for _ in range(60):  # 视频要后台处理 → 等转 ACTIVE
            f = client.files.get(name=f.name)
            if f.state == "ACTIVE":
                break
            if f.state == "FAILED":
                raise RuntimeError(f"Gemini 文件处理 FAILED: {f.name}")
            time.sleep(2)
        else:
            raise RuntimeError("Gemini 文件 60×2s 仍未 ACTIVE")
        _GEMINI_FILE_CACHE[youtube_id] = f
        return f


def call_gemini(user_prompt: str, model: str = "gemini-3.5-flash", images: list[bytes] | None = None,
                video_youtube_id: str | None = None) -> dict:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        max_output_tokens=MAX_TOKENS,
        response_mime_type="application/json",
    )
    # var B: text + 关键帧图像 (Gemini 用 Part.from_bytes); var D: text + 原生视频 (Files API)
    contents = user_prompt
    if video_youtube_id:
        vid = _get_gemini_video(client, video_youtube_id)
        contents = [vid, user_prompt]
    elif images:
        contents = [user_prompt] + [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images]
    # 503 瞬时过载 → 指数退避; 429 RESOURCE_EXHAUSTED (视频 token 大易撞 TPM) → 更长退避扛过窗口
    for attempt in range(5):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents, config=cfg)
            break
        except genai_errors.ServerError as e:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)  # 3,6,12,24s
        except genai_errors.ClientError as e:
            # 429/配额: TPM 分钟级窗口 → 长退避扛过; 仍超 (daily 配额) 则 raise 留续跑. 其他 4xx 不退避.
            if attempt == 4 or ("429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e)):
                raise
            time.sleep(2 ** attempt * 15)  # 15,30,60,120s
    else:
        raise RuntimeError("gemini 重试耗尽")
    um = resp.usage_metadata
    tin = um.prompt_token_count or 0
    thoughts = getattr(um, "thoughts_token_count", None) or 0
    cand = um.candidates_token_count or 0
    return {"text": resp.text or "", "tokens_in": tin, "tokens_out": cand + thoughts,
            "reasoning_tokens": thoughts, "model_version": model}


def call_openai_compat(model: str, user_prompt: str, images: list[bytes] | None = None,
                       video_youtube_id: str | None = None) -> dict:
    import base64
    from openai import OpenAI
    env_key, base_url = OPENAI_COMPAT[model]
    # 短 timeout + 关内置重试: 慢请求快速失败靠 generate_row 自己重试 (防卡死, 见 kimi var D 卡死一夜).
    # kimi 视频单条正常 130-310s(最慢~800s) → timeout 500 覆盖正常 + 抓真卡死; 其余(纯文本)快, 120 足够.
    _to = 500 if model == "kimi-k2.6" else 120
    client = OpenAI(api_key=os.environ[env_key], base_url=base_url, timeout=_to, max_retries=0)
    # var D: 原生视频 (Kimi 内联 base64 video_url, judge_probe 验通; ~22MB → base64 每条编码)
    if video_youtube_id:
        path = VIDEO_DIR / f"{video_youtube_id}.mp4"
        b64v = base64.b64encode(path.read_bytes()).decode()
        user_content = [
            {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64v}"}},
            {"type": "text", "text": user_prompt},
        ]
    # var B: text + 图像 (OpenAI 兼容用 content 数组 + image_url data URI base64)
    elif images:
        user_content = [{"type": "text", "text": user_prompt}]
        for img in images:
            b64 = base64.b64encode(img).decode()
            user_content.append({"type": "image_url",
                                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    else:
        user_content = user_prompt
    # 不设 temperature (reasoning 模型用默认); 用 max_completion_tokens (GPT-5 系) / max_tokens 回退
    kw = {"model": model,
          "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                       {"role": "user", "content": user_content}]}
    # Kimi 关 thinking (user 决策): 默认 thinking 为一条 120 字文案烧 4700 token/130s, 不可行.
    #   实测正确参数 = extra_body={"thinking":{"type":"disabled"}} (enable_thinking=False 无效).
    #   关后 ~16s / 40 token. gpt/deepseek 保留默认 thinking. design-doc § 4.2 需注明此偏离.
    if model == "kimi-k2.6":
        kw["extra_body"] = {"thinking": {"type": "disabled"}}
    try:
        resp = client.chat.completions.create(max_completion_tokens=MAX_TOKENS, **kw)
    except Exception:
        resp = client.chat.completions.create(max_tokens=MAX_TOKENS, **kw)
    msg = resp.choices[0].message
    u = resp.usage
    reasoning = 0
    det = getattr(u, "completion_tokens_details", None)
    if det is not None:
        reasoning = getattr(det, "reasoning_tokens", 0) or 0
    return {"text": msg.content or "", "tokens_in": u.prompt_tokens,
            "tokens_out": u.completion_tokens, "reasoning_tokens": reasoning,
            "model_version": getattr(resp, "model", model)}


def call_model(model: str, user_prompt: str, images: list[bytes] | None = None,
               video_youtube_id: str | None = None) -> dict:
    if model.startswith("gemini"):
        return call_gemini(user_prompt, model, images, video_youtube_id)
    return call_openai_compat(model, user_prompt, images, video_youtube_id)


# ─────────────────────────────────────────────────────────
# 解析 + 成本 + 合规
# ─────────────────────────────────────────────────────────

def parse_output(text: str) -> tuple[str, str, bool]:
    """从模型输出抠 title/body. 返回 (title, body, parse_ok)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()  # 去 markdown fence
    m = re.search(r"\{.*\}", t, flags=re.S)  # 第一个 {...}
    if m:
        try:
            d = json.loads(m.group(0))
            return str(d.get("title", "")).strip(), str(d.get("body", "")).strip(), True
        except json.JSONDecodeError:
            pass
    return "", t[:200], False  # 解析失败: 原文截断进 body, 标记


def cost_usd(model: str, tin: int, tout: int) -> float:
    pi, po = PRICES[model]
    return (tin * pi + tout * po) / 1e6


# ─────────────────────────────────────────────────────────
# 全量: 单次生成 → OutputRow; 并发 + 落盘 + 断点续跑
# ─────────────────────────────────────────────────────────

def generate_row(case: dict, model: str, run_id: int, var: str = "A", max_retries: int = 2) -> OutputRow | None:
    """一次 (case, model, run_id, var) 生成 → OutputRow. 失败重试 max_retries 次; 仍失败返回 None (不落盘, 留待续跑).

    var A: metadata-only 文本. var B: metadata + 关键帧图像 (按 vendor 格式封装).
    cell_id / var_label 按 var 参数化 → 跟 var A 互不冲突 (落独立文件 + 标签 + cell_id 三重隔离).
    """
    up = render_user_prompt(case, var)
    images = None
    video_youtube_id = None
    pp_meta = PreprocessMeta()
    if var == "B":
        images = load_keyframes(case["video"]["video_id"])
        if not images:
            print(f"  [FAIL] {model}|{case['case_id']}|r{run_id}: var B 但无关键帧")
            return None
    elif var == "C":
        pp_meta = transcript_meta(case["video"]["video_id"])  # 语种/词数/置信度落表
    elif var == "D":
        if model not in VIDEO_MODELS:  # gpt/deepseek 纯文本看不了视频 → 不该进 var D
            print(f"  [FAIL] {model}|{case['case_id']}|r{run_id}: var D 但 {model} 不支持视频")
            return None
        video_youtube_id = case["video"]["youtube_id"]
        if not (VIDEO_DIR / f"{video_youtube_id}.mp4").exists():
            print(f"  [FAIL] {model}|{case['case_id']}|r{run_id}: var D 但视频文件缺失 {video_youtube_id}.mp4")
            return None
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            r = call_model(model, up, images, video_youtube_id)
            latency = int((time.time() - t0) * 1000)
            title, body, ok = parse_output(r["text"])
            return OutputRow(
                cell_id=f"{model}|{var}|{case['case_id']}",
                case_id=case["case_id"],
                run_id=run_id,
                model=model,
                var_label=VarLabel(var),
                video_id=case["video"]["video_id"],
                persona_id=case["persona"]["persona_id"],
                generated_at=datetime.now(timezone.utc),
                output_title=title,
                output_body=body,
                tokens_in=r["tokens_in"],
                tokens_out=r["tokens_out"],
                latency_ms=latency,
                cost_usd=cost_usd(model, r["tokens_in"], r["tokens_out"]),
                model_version=r["model_version"],
                length_compliant=(len(title) <= 50 and len(body) <= 150),
                preprocess_meta=pp_meta,
                auto_evidence={"parse_ok": ok, "reasoning_tokens": r["reasoning_tokens"],
                               "n_keyframes": len(images) if images else 0,
                               "video_attached": bool(video_youtube_id)},
            )
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 ** attempt * 2)  # 2,4s
    print(f"  [FAIL] {model}|{var}|{case['case_id']}|r{run_id}: {type(last_err).__name__}: {str(last_err)[:90]}")
    return None


def _load_done(out_path: Path) -> set[tuple[str, int]]:
    """已落盘的 (cell_id, run_id), 用于续跑跳过."""
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                d = json.loads(line)
                done.add((d["cell_id"], d["run_id"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def run_full(models: list[str], reps: int, limit_cases: int, workers: int, out_path: Path,
             var: str = "A", sort_by_size: bool = False):
    cases = [json.loads(l) for l in TEST_CASES.open(encoding="utf-8")]
    if sort_by_size:  # 小视频先跑 (上传量小, 弱网下不易 timeout; 大视频留后/换网络)
        def _vsz(c):
            p = VIDEO_DIR / f"{c['video']['youtube_id']}.mp4"
            return p.stat().st_size if p.exists() else 1 << 60
        cases.sort(key=_vsz)
    if limit_cases:
        cases = cases[:limit_cases]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(out_path)

    tasks = [(c, m, r) for c in cases for m in models for r in range(1, reps + 1)
             if (f"{m}|{var}|{c['case_id']}", r) not in done]
    total_target = len(cases) * len(models) * reps
    print(f"[run] var={var} | {len(cases)} case × {len(models)} 模型 × {reps} reps = {total_target} 目标 cell")
    print(f"[run] 已完成 {len(done)} (续跑跳过), 本轮跑 {len(tasks)}, 并发 {workers} → {out_path}")
    if not tasks:
        print("[run] 全部已完成, 无需跑.")
        return

    lock = threading.Lock()
    n_ok = n_fail = 0
    cost_sum = 0.0
    t_start = time.time()
    with out_path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(generate_row, c, m, r, var): (m, c["case_id"], r) for c, m, r in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            with lock:
                if row is None:
                    n_fail += 1
                else:
                    f.write(row.model_dump_json() + "\n")
                    f.flush()
                    n_ok += 1
                    cost_sum += row.cost_usd
            if i % 20 == 0 or i == len(tasks):
                el = time.time() - t_start
                rate = i / el if el else 0
                eta = (len(tasks) - i) / rate if rate else 0
                print(f"  {i}/{len(tasks)} | ok={n_ok} fail={n_fail} | ${cost_sum:.2f} | "
                      f"{rate:.1f}/s | ETA {eta/60:.0f}min")

    print(f"\n[OK] 本轮 ok={n_ok} fail={n_fail} | 成本 ${cost_sum:.2f} | "
          f"用时 {(time.time()-t_start)/60:.1f}min → {out_path}")
    if n_fail:
        print(f"     {n_fail} 条失败 (未落盘); 再跑一次本命令会续跑这些 (resume)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["pilot", "run"])
    ap.add_argument("--n", type=int, default=3, help="pilot 每模型跑几个 case")
    ap.add_argument("--models", nargs="*", default=list(PRICES.keys()))
    ap.add_argument("--reps", type=int, default=3, help="run: 每 cell 重复次数")
    ap.add_argument("--limit-cases", type=int, default=0, help="run: 只跑前 N case (0=全部); 小批验证用")
    ap.add_argument("--workers", type=int, default=8, help="run: 并发数")
    ap.add_argument("--var", choices=["A", "B", "C", "D"], default="A", help="输入变量 (A=metadata / B=+keyframes)")
    ap.add_argument("--out", default=None, help="run: 落盘路径 (默认 results/var_{VAR}_raw.jsonl)")
    ap.add_argument("--sort-size", action="store_true", help="run: 按视频大小升序 (小视频先, 弱网下用)")
    args = ap.parse_args()

    if args.mode == "run":
        out = Path(args.out) if args.out else Path(f"results/var_{args.var}_raw.jsonl")
        run_full(args.models, args.reps, args.limit_cases, args.workers, out, args.var, args.sort_size)
        return

    cases = [json.loads(l) for l in TEST_CASES.open(encoding="utf-8")]
    # 取覆盖不同分类的前 n 个 (按 video_id 去重取不同视频)
    seen_v, picked = set(), []
    for c in cases:
        vid = c["video"]["video_id"]
        if vid not in seen_v:
            seen_v.add(vid); picked.append(c)
        if len(picked) >= args.n:
            break

    print(f"=== var {args.var} pilot: {len(picked)} case × {len(args.models)} 模型 ===\n")
    print("样例输入 prompt (case 1):")
    print("--- SYSTEM ---\n" + SYSTEM_PROMPT)
    print("--- USER ---\n" + render_user_prompt(picked[0], args.var) + "\n" + "=" * 70)

    rows, total_cost = [], 0.0
    for c in picked:
        up = render_user_prompt(c, args.var)
        for model in args.models:
            t0 = time.time()
            try:
                r = call_model(model, up)
            except Exception as e:
                print(f"\n[{c['case_id']}] {model}  ERROR: {type(e).__name__}: {str(e)[:150]}")
                rows.append({"model": model, "error": str(e)[:80]})
                continue
            latency = int((time.time() - t0) * 1000)
            title, body, ok = parse_output(r["text"])
            cost = cost_usd(model, r["tokens_in"], r["tokens_out"])
            total_cost += cost
            lc = len(title) <= 50 and len(body) <= 150
            rows.append({"model": model, "tin": r["tokens_in"], "tout": r["tokens_out"],
                         "reason": r["reasoning_tokens"], "cost": cost, "parse_ok": ok, "lc": lc})
            print(f"\n[{c['case_id']}] {model}")
            print(f"  tokens: in={r['tokens_in']} out={r['tokens_out']} (reasoning={r['reasoning_tokens']}) "
                  f"| {latency}ms | ${cost:.5f} | parse={'OK' if ok else 'FAIL'} | len_ok={lc}")
            print(f"  title ({len(title)}c): {title!r}")
            print(f"  body  ({len(body)}c): {body!r}")

    # 汇总 + 全量外推
    print("\n" + "=" * 70 + "\n=== 汇总 (实测) ===")
    ok_rows = [r for r in rows if "error" not in r]
    by_model = {}
    for r in ok_rows:
        by_model.setdefault(r["model"], []).append(r)
    FULL_CALLS = 416 * 3  # 全量 var A 每模型
    print(f"{'model':18}{'n':>3}{'avg_in':>8}{'avg_out':>8}{'avg_reason':>11}{'$/call':>9}{'全量$(×1248)':>14}")
    grand = 0.0
    for m, rs in by_model.items():
        ai = sum(r["tin"] for r in rs) / len(rs)
        ao = sum(r["tout"] for r in rs) / len(rs)
        ar = sum(r["reason"] for r in rs) / len(rs)
        pc = sum(r["cost"] for r in rs) / len(rs)
        full = pc * FULL_CALLS
        grand += full
        print(f"{m:18}{len(rs):>3}{ai:>8.0f}{ao:>8.0f}{ar:>11.0f}{pc:>9.5f}{full:>13.1f}")
    print(f"\n全量 var A (4 模型 × 1248) 实测外推总成本 ≈ ${grand:.1f}")
    print(f"parse 成功率: {sum(r['parse_ok'] for r in ok_rows)}/{len(ok_rows)} | "
          f"长度合规: {sum(r['lc'] for r in ok_rows)}/{len(ok_rows)}")


if __name__ == "__main__":
    main()
