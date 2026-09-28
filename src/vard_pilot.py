"""var D pilot: 跑前估成本 + 验视频传输 (handoff 硬约束: 视频 token 贵, 全量前必须先 pilot).

为什么不用 run --limit-cases: 它取前 N case 全是 V01(22MB) 同一视频, 估不出大视频成本,
也验不出 Kimi 内联 base64 对大视频(56MB→base64 75MB)是否撞上限.

挑 3 个不同大小视频 (最小/中位/最大) × 2 视频模型 × run_id=1 = 6 次 generate_row.
逐条打印 tin/tout/$/latency/parse, 再按 32 视频实际大小线性外推全量 (416×3×2).

CLI: PYTHONPATH=src .venv/bin/python src/vard_pilot.py
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

import run_generation as RG

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

# 手挑 3 个视频 (youtube_id), 覆盖大小跨度 (实测 ls -la)
PILOT_VIDEOS = [
    ("RBaSiVjtKR4", 6.4),    # 最小
    ("vP6DbSjjQ1E", 22.5),   # 中位
    ("pOfW-qdsvpU", 56.1),   # 最大 — 验 Kimi 内联 base64 上限
]
MODELS = ["gemini-2.5-flash", "kimi-k2.6"]


def load_cases_by_youtube() -> dict:
    """youtube_id → 第一个该视频的 case (任意 persona, 取 run_id=1)."""
    by = {}
    for l in Path("data/test_cases.jsonl").open(encoding="utf-8"):
        c = json.loads(l)
        yid = c["video"]["youtube_id"]
        if yid not in by:
            by[yid] = c
    return by


def all_video_sizes_mb() -> list[float]:
    return sorted(p.stat().st_size / 1e6 for p in RG.VIDEO_DIR.glob("*.mp4"))


def main():
    cases = load_cases_by_youtube()
    print("=" * 74)
    print("var D PILOT — 3 视频(小/中/大) × 2 模型, run_id=1, 估成本 + 验传输")
    print("=" * 74)

    pts = []  # (mb, model, tin, tout, cost)
    for yid, mb in PILOT_VIDEOS:
        case = cases.get(yid)
        if not case:
            print(f"\n[{yid}] ⚠️ test_cases 里没找到该视频, 跳过")
            continue
        print(f"\n{'─'*74}\n[视频 {case['video']['video_id']} = {yid}] {mb}MB | {case['video']['video_title'][:50]}")
        for model in MODELS:
            print(f"\n  ── {model} ──")
            try:
                row = RG.generate_row(case, model, run_id=1, var="D", max_retries=1)
            except Exception as ex:
                print(f"    [ERROR] {type(ex).__name__}: {str(ex)[:200]}")
                continue
            if row is None:
                print("    [FAIL] generate_row 返回 None (见上方 FAIL 行)")
                continue
            ev = row.auto_evidence or {}
            print(f"    {row.latency_ms/1000:.1f}s | tin={row.tokens_in} tout={row.tokens_out} | "
                  f"${row.cost_usd:.5f} | parse={'OK' if ev.get('parse_ok') else 'FAIL'} | "
                  f"video={ev.get('video_attached')}")
            print(f"    T: {row.output_title}")
            print(f"    B: {row.output_body}")
            pts.append((mb, model, row.tokens_in, row.tokens_out, row.cost_usd))

    # ── 外推: 每模型按 tin≈k*MB 线性拟合, 对 32 视频实际大小估全量 ──
    print(f"\n{'='*74}\n全量外推 (416 case × 3 rep × 模型; 每视频 13 persona × 3 = 39 cell)")
    sizes = all_video_sizes_mb()
    print(f"  32 视频大小: min={sizes[0]:.1f} 中位={sizes[len(sizes)//2]:.1f} max={sizes[-1]:.1f} 合计={sum(sizes):.0f}MB")
    for model in MODELS:
        mp = [(mb, ti, to, c) for mb, m, ti, to, c in pts if m == model]
        if len(mp) < 2:
            print(f"\n  {model}: pilot 点不足 ({len(mp)}), 无法外推")
            continue
        # tin ~ k*MB (过原点近似: 视频 token 主导, 文本 prompt ~几百 token 占比小)
        k_tin = sum(ti for mb, ti, to, c in mp) / sum(mb for mb, ti, to, c in mp)
        avg_tout = sum(to for mb, ti, to, c in mp) / len(mp)
        pi, po = RG.PRICES[model]
        # 每视频成本 = (k_tin*MB*pi + avg_tout*po)/1e6, ×39 cell, 累加 32 视频
        total = sum((k_tin * mb * pi + avg_tout * po) / 1e6 * 39 for mb in sizes)
        print(f"\n  {model}: k_tin≈{k_tin:.0f} token/MB | avg_tout≈{avg_tout:.0f} | "
              f"价 in/out={pi}/{po}")
        print(f"    → 全量 1248 cell 估 ≈ ${total:.1f}")
    print("\n  注: 线性外推近似 (视频→token 实际非严格线性); 全量真跑以 resume 落盘累计为准.")


if __name__ == "__main__":
    main()
