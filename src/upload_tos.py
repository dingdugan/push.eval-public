"""W3: 把 32 个视频传到火山 TOS, 供 Doubao judge 用 http URL 拉取.

为什么 URL 不内联 (实测依据见 worklog 2026-06-19):
  (1) 内联 base64 大视频超火山体积上限 (~base64 70MB → 最大 3 个视频 413 Request Entity Too Large);
  (2) 内联每条评都重传视频, judge 16185 条 × 中位 22MB ≈ 356GB 重复上传.
  URL 方式: 视频传一次 (~800MB), judge 请求体只含 URL, Doubao 北京内网拉 → 绕过两者.

执行环境: 公司网络下跑 (到 TOS 快; 家用美国网络上传墙, 见 worklog). 幂等 — 已传的跳过, 可断点续传.
key = <youtube_id>.mp4, 与 doubao_judge_probe.doubao_video_url() 构造的 URL 对齐.
桶需 "公共读" (Doubao 服务端免签名拉取).

CLI: PYTHONPATH=src .venv/bin/python src/upload_tos.py [--bucket push-eval-videos] [--verify]
  --verify: 传完端到端验证 Doubao 真能从 TOS URL 拉到视频.
"""

from __future__ import annotations

import argparse
import glob
import os
import time
from pathlib import Path

import tos
from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

ENDPOINT = "tos-cn-beijing.volces.com"   # 华北2(北京), 与 Doubao ark.cn-beijing 同区
REGION = "cn-beijing"
VIDEO_DIR = Path("data/videos")


def client() -> "tos.TosClientV2":
    # socket_timeout 给大: 大视频(~56MB)上传慢; connection_time 短: 连不上快失败
    return tos.TosClientV2(os.environ["VOLC_ACCESS_KEY"], os.environ["VOLC_SECRET_KEY"],
                           ENDPOINT, REGION, connection_time=30, socket_timeout=600)


def upload_all(bucket: str, verify: bool):
    cli = client()
    vids = sorted(glob.glob(str(VIDEO_DIR / "*.mp4")))
    if not vids:
        print(f"[err] {VIDEO_DIR} 下没有 mp4"); return
    total_mb = sum(os.path.getsize(v) for v in vids) / 1e6
    try:
        existing = {o.key for o in (cli.list_objects_type2(bucket).contents or [])}
    except Exception as e:
        print(f"[err] list bucket 失败 (检查 bucket 名 / 权限 / 网络): {type(e).__name__}: {str(e)[:120]}")
        return
    print(f"{len(vids)} 个视频 (共 {total_mb:.0f}MB), TOS 桶 '{bucket}' 已有 {len(existing)} 个")
    up_n = up_mb = 0
    t_all = time.time()
    for path in vids:
        key = Path(path).name                # = <youtube_id>.mp4
        mb = os.path.getsize(path) / 1e6
        if key in existing:
            print(f"  跳过(已传): {key}")
            continue
        t = time.time()
        try:
            cli.put_object_from_file(bucket, key, path)
            dt = time.time() - t
            up_n += 1; up_mb += mb
            print(f"  ✅ {key:18} {mb:5.1f}MB {dt:4.0f}s ({mb/dt:.1f}MB/s)")
        except Exception as e:
            print(f"  ❌ {key:18} {mb:5.1f}MB {time.time()-t:4.0f}s {type(e).__name__}: {str(e)[:80]}")
    print(f"\n[本轮] 传 {up_n} 个 / {up_mb:.0f}MB, 用时 {(time.time()-t_all)/60:.1f}min")
    # 复核: 桶里齐不齐 (断点续传可重跑直到齐)
    final = {o.key for o in (cli.list_objects_type2(bucket).contents or [])}
    missing = [Path(v).name for v in vids if Path(v).name not in final]
    if missing:
        print(f"[复核] 桶内 {len(final)}/{len(vids)} — 缺 {len(missing)} 个: {missing}\n       (重跑本命令续传)")
    else:
        print(f"[复核] 桶内 {len(final)}/{len(vids)} ✅ 齐")
        if verify:
            verify_doubao_pull(bucket, Path(vids[0]).name)


def verify_doubao_pull(bucket: str, key: str):
    """端到端验证 Doubao 服务端真能从 TOS URL 拉到并理解视频."""
    from openai import OpenAI
    url = f"https://{bucket}.{ENDPOINT}/{key}"
    print(f"\n[验证] Doubao 拉 {url}")
    c = OpenAI(api_key=os.environ["DOUBAO_API_KEY"],
               base_url="https://ark.cn-beijing.volces.com/api/v3", timeout=120)
    t = time.time()
    try:
        r = c.chat.completions.create(
            model="doubao-seed-2-0-lite-260428",
            messages=[{"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": url}},
                {"type": "text", "text": "one sentence: what is this video about?"}]}],
            max_tokens=40)
        print(f"  ✅ Doubao 拉取+理解 {time.time()-t:.0f}s — {(r.choices[0].message.content or '').strip()[:70]}")
        print("  → URL 链路通, 可跑 doubao_judge_probe.py 再 ops/run_judge_full.sh")
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {str(e)[:140]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default="push-eval-videos")
    ap.add_argument("--verify", action="store_true", help="传完验证 Doubao 能拉 TOS URL")
    a = ap.parse_args()
    upload_all(a.bucket, a.verify)


if __name__ == "__main__":
    main()
