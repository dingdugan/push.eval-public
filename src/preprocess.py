"""Day 9 预处理: 32 视频 → 关键帧 (var B) + 音轨转写 (var C). 2026-05-31.

pipeline (兑现 design-doc § 2.1 + schema PreprocessMeta):
  1. yt-dlp 下载视频 (≤720p, 含音轨) → data/videos/{youtube_id}.mp4
  2. PySceneDetect (ContentDetector, HSV 直方图差分) 检测镜头 → scene 列表
  3. 每 scene 取中间帧 (ffmpeg 抽); cap 5 — scene>5 取时长最长的 5 个 (按时间排序命名),
     scene≤5 全抽 → data/preprocess/keyframes/{video_id}/frame_{i}.jpg
  4. Whisper-large-v3 转写音轨 → data/preprocess/transcripts/{video_id}.json
     (segments + 时间戳 + avg_logprob)
  5. 填 PreprocessMeta: keyframe_count / scene_count / transcript_word_count /
     transcript_confidence (mean avg_logprob) / audio_speech_ratio (语音段占比)

CLI:
  python src/preprocess.py pilot              # 跑 PILOT_IDS (覆盖分类/时长/体裁)
  python src/preprocess.py pilot --only V13   # 只跑某条 (端到端 smoke)
  python src/preprocess.py batch              # 全 32 条 (pilot 通过后)
  python src/preprocess.py batch --device cpu # 强制 CPU (MPS 出问题时)

断点续跑: keyframes 目录非空 + transcript json 存在 = 跳过.
依赖: yt-dlp + ffmpeg (CLI), torch/whisper/scenedetect (venv). 见 requirements.txt.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
from pathlib import Path

TRANSCRIBE_TIMEOUT_SEC = int(os.environ.get("TRANSCRIBE_TIMEOUT_SEC", "1200"))  # 单条转写超时(秒); env 可覆盖
# WHISPER_TEMPERATURE: 设为 "0" 关温度回退 → 噪声/音乐视频提速 ~6x (默认不设 = whisper 自带回退档)
_WHISPER_TEMP_ENV = os.environ.get("WHISPER_TEMPERATURE")


class _TranscribeTimeout(Exception):
    pass

# Whisper MPS 部分算子未实现 → 允许回落 CPU, 避免中途 crash
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

VIDEOS_JSONL = Path("data/videos.jsonl")
VIDEO_DIR = Path("data/videos")
KEYFRAME_DIR = Path("data/preprocess/keyframes")
TRANSCRIPT_DIR = Path("data/preprocess/transcripts")
META_OUT = Path("data/preprocess/preprocess_meta.jsonl")

KEYFRAME_CAP = 5
WHISPER_MODEL = "large-v3"

# pilot: 5 条覆盖 分类 × 时长 × 体裁 (talking-head / 快剪 / 文字密集)
PILOT_IDS = ["V13", "V01", "V17", "V21", "V25"]
#            news长讲  音乐快剪 教育   生活技巧  体育快剪


# ─────────────────────────────────────────────────────────
# 加载视频清单
# ─────────────────────────────────────────────────────────

def load_videos() -> dict[str, dict]:
    """video_id → record (含 youtube_id / duration_sec / category)."""
    if not VIDEOS_JSONL.exists():
        sys.exit(f"[FATAL] 缺 {VIDEOS_JSONL}, 先跑 curate_videos.py")
    return {json.loads(l)["video_id"]: json.loads(l) for l in VIDEOS_JSONL.open(encoding="utf-8")}


# ─────────────────────────────────────────────────────────
# 1. 下载
# ─────────────────────────────────────────────────────────

def download_video(youtube_id: str) -> Path | None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    out = VIDEO_DIR / f"{youtube_id}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    # YT_COOKIES_FROM_BROWSER (如 "chrome"): 用登录态绕过 "confirm you're not a bot" 软封禁.
    # 节流 sleep 3-8s: 连下多条防再触发 IP 反爬 (W2 实测连下 28 条被封).
    cookie_args = []
    ck = os.environ.get("YT_COOKIES_FROM_BROWSER")
    if ck:
        cookie_args = ["--cookies-from-browser", ck]
    cmd = [
        "yt-dlp", "--quiet", "--no-warnings", "--no-playlist",
        *cookie_args,
        "--sleep-interval", "3", "--max-sleep-interval", "8",
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--merge-output-format", "mp4",
        "-o", str(out),
        f"https://www.youtube.com/watch?v={youtube_id}",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print(f"    [WARN] 下载超时 {youtube_id}")
        return None
    if p.returncode != 0 or not out.exists():
        print(f"    [WARN] 下载失败 {youtube_id} (rc={p.returncode}): {p.stderr[:120]}")
        return None
    return out


# ─────────────────────────────────────────────────────────
# 2. 镜头检测 + 3. 抽中间帧
# ─────────────────────────────────────────────────────────

def detect_scenes(video_path: Path) -> list[tuple[float, float]]:
    """ContentDetector 检镜头. 返回 [(start_sec, end_sec)]; 无切换则整片 1 个 scene."""
    from scenedetect import detect, ContentDetector
    scenes = detect(str(video_path), ContentDetector())
    if not scenes:
        return []  # caller 兜底成单 scene
    return [(s.get_seconds(), e.get_seconds()) for s, e in scenes]


def pick_scenes(scenes: list[tuple[float, float]], cap: int) -> list[tuple[float, float]]:
    """scene>cap: 取时长最长的 cap 个 (代表性最强), 再按时间排序; ≤cap 全取."""
    if len(scenes) <= cap:
        return scenes
    longest = sorted(scenes, key=lambda x: -(x[1] - x[0]))[:cap]
    return sorted(longest, key=lambda x: x[0])


def extract_keyframe(video_path: Path, t_sec: float, out_path: Path) -> bool:
    """ffmpeg 在 t_sec 抽 1 帧 jpg."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{t_sec:.2f}", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(out_path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return p.returncode == 0 and out_path.exists()


def make_keyframes(video_path: Path, video_id: str, total_dur: float) -> tuple[int, int]:
    """返回 (scene_count, keyframe_count)."""
    scenes = detect_scenes(video_path)
    if not scenes:
        scenes = [(0.0, total_dur)]  # 无镜头切换 = 单 scene
    scene_count = len(scenes)
    picked = pick_scenes(scenes, KEYFRAME_CAP)

    out_dir = KEYFRAME_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # 清旧帧 (重跑)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()

    kf = 0
    for i, (s, e) in enumerate(picked, 1):
        mid = (s + e) / 2.0
        if extract_keyframe(video_path, mid, out_dir / f"frame_{i}.jpg"):
            kf += 1
    return scene_count, kf


# ─────────────────────────────────────────────────────────
# 4. 转写 (Whisper-large-v3)
# ─────────────────────────────────────────────────────────

_WHISPER_MODEL_CACHE = {}


def get_whisper(device: str):
    if device not in _WHISPER_MODEL_CACHE:
        import whisper
        print(f"    [whisper] 加载 {WHISPER_MODEL} on {device} (首次下载 ~3GB)...")
        _WHISPER_MODEL_CACHE[device] = whisper.load_model(WHISPER_MODEL, device=device)
    return _WHISPER_MODEL_CACHE[device]


def transcribe(video_path: Path, video_id: str, total_dur: float, device: str) -> dict:
    """转写 → 落 json; 返回 PreprocessMeta 的 transcript_* 字段."""
    model = get_whisper(device)
    # 不强制语种: 让 Whisper 自动检测 (英语过滤只作用在 metadata, 音频可能非英语,
    #   如音乐 MV; 强制 en 会把非英语音频音译成乱码). fp16 仅 CUDA; MPS/CPU 用 fp32.
    # condition_on_previous_text=False: 防 Whisper 在音乐/歌唱音频上陷入重复循环
    #   (实测 V01 印地语 MV 转写慢 5-6x); 关掉跨窗上下文显著加速 + 避免假死.
    _kw = {}
    if _WHISPER_TEMP_ENV is not None:
        _kw["temperature"] = float(_WHISPER_TEMP_ENV)  # 关回退提速 (噪声/音乐视频)
    result = model.transcribe(str(video_path), fp16=False, verbose=False,
                              condition_on_previous_text=False, **_kw)
    lang = result.get("language")

    segs = [
        {"start": round(s["start"], 2), "end": round(s["end"], 2),
         "text": s["text"].strip(), "avg_logprob": round(s.get("avg_logprob", 0.0), 4)}
        for s in result.get("segments", [])
    ]
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    (TRANSCRIPT_DIR / f"{video_id}.json").write_text(
        json.dumps({"video_id": video_id, "language": lang,
                    "text": result.get("text", "").strip(), "segments": segs},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    full_text = result.get("text", "").strip()
    word_count = len(full_text.split())
    confidence = (sum(s["avg_logprob"] for s in segs) / len(segs)) if segs else None
    speech_dur = sum(s["end"] - s["start"] for s in segs)
    speech_ratio = min(speech_dur / total_dur, 1.0) if total_dur > 0 else None
    return {
        "transcript_language": lang,  # 自动检测; 非 "en" = 音频非英语, batch 后汇总给 user
        "transcript_word_count": word_count,
        "transcript_confidence": round(confidence, 4) if confidence is not None else None,
        "audio_speech_ratio": round(speech_ratio, 3) if speech_ratio is not None else None,
    }


# ─────────────────────────────────────────────────────────
# 单条 pipeline
# ─────────────────────────────────────────────────────────

def process_one(rec: dict, device: str, force: bool = False) -> dict | None:
    vid = rec["video_id"]
    yt = rec["youtube_id"]
    dur = float(rec.get("duration_sec") or 0)
    kf_dir = KEYFRAME_DIR / vid
    tr_path = TRANSCRIPT_DIR / f"{vid}.json"
    done = (not force) and tr_path.exists() and kf_dir.exists() and any(kf_dir.glob("frame_*.jpg"))
    if done:
        print(f"  {vid} [{rec['category']}] → skip (已处理)")
        return None

    print(f"  {vid} [{rec['category']}] {yt} ({dur:.0f}s) ...")
    vpath = download_video(yt)
    if vpath is None:
        return {"video_id": vid, "error": "download_failed"}

    scene_count, kf = make_keyframes(vpath, vid, dur)
    print(f"    scenes={scene_count} keyframes={kf}")

    # 转写加超时网: 超 TRANSCRIBE_TIMEOUT_SEC 抛异常 → run() 逐条 catch → 该条记错跳过, batch 续跑
    def _on_alarm(signum, frame):
        raise _TranscribeTimeout(f"转写超 {TRANSCRIBE_TIMEOUT_SEC}s")
    old_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(TRANSCRIBE_TIMEOUT_SEC)
    try:
        tr = transcribe(vpath, vid, dur, device)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
    print(f"    words={tr['transcript_word_count']} conf={tr['transcript_confidence']} "
          f"speech_ratio={tr['audio_speech_ratio']}")

    return {"video_id": vid, "youtube_id": yt, "category": rec["category"],
            "scene_count": scene_count, "keyframe_count": kf, **tr}


def run(video_ids: list[str], device: str, force: bool):
    videos = load_videos()
    targets = [videos[v] for v in video_ids if v in videos]
    missing = [v for v in video_ids if v not in videos]
    if missing:
        print(f"[WARN] 不在 videos.jsonl: {missing}")
    print(f"[preprocess] {len(targets)} 视频, device={device}, model={WHISPER_MODEL}")

    META_OUT.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for rec in targets:
        try:
            r = process_one(rec, device, force)
            if r:
                results.append(r)
        except Exception as e:
            print(f"  [ERROR] {rec['video_id']}: {type(e).__name__}: {str(e)[:120]}")
            results.append({"video_id": rec["video_id"], "error": f"{type(e).__name__}: {str(e)[:80]}"})

    # append-merge 到 meta (按 video_id 去重, 新结果覆盖旧)
    existing = {}
    if META_OUT.exists():
        for l in META_OUT.open(encoding="utf-8"):
            d = json.loads(l); existing[d["video_id"]] = d
    for r in results:
        existing[r["video_id"]] = r
    with META_OUT.open("w", encoding="utf-8") as f:
        for vid in sorted(existing):
            f.write(json.dumps(existing[vid], ensure_ascii=False) + "\n")

    ok = [r for r in results if "error" not in r]
    err = [r for r in results if "error" in r]
    print(f"\n[OK] 本轮处理 {len(ok)} 成功 / {len(err)} 失败 → meta: {META_OUT}")
    if err:
        print(f"     失败: {[(r['video_id'], r['error']) for r in err]}")


def auto_device(arg: str) -> str:
    """auto → cpu. Whisper 在 MPS 上撞 SparseMPS 未实现 (aten::_sparse_coo_tensor...),
    MPS_FALLBACK 接不住该 op (实测 2026-05-31). 故 Whisper 固定 CPU; 想试 MPS 显式 --device mps.
    (镜头检测/抽帧不用 torch device, 不受影响.)"""
    if arg != "auto":
        return arg
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Day 9 预处理: 关键帧 + Whisper 转写")
    ap.add_argument("mode", choices=["pilot", "batch"])
    ap.add_argument("--only", help="只跑某个 video_id (端到端 smoke)")
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cpu", "cuda"])
    ap.add_argument("--force", action="store_true", help="重跑已处理的")
    args = ap.parse_args()

    if args.only:
        ids = [x.strip() for x in args.only.split(",") if x.strip()]  # 支持逗号多值
    elif args.mode == "pilot":
        ids = PILOT_IDS
    else:
        ids = [f"V{i:02d}" for i in range(1, 33)]

    run(ids, auto_device(args.device), args.force)
