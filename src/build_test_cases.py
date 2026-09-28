"""Day 10: 笛卡尔积 32 video × 13 persona = 416 test case + 派生 B0/B1 baseline.

→ data/test_cases.jsonl (每行一个 TestCase, 含 video / persona / trigger / baseline).

Baseline 派生规则 (design-doc § 2.4, 每视频一份, 不分 persona):
  B0 现状: title=`{channel} just posted:` / body=`{video_title}`
           — 现状生产模板 (creator 直发 + 系统不改写, 即 § 1.2 反例)
  B1 规则: title=`New video | {category} · {channel}`
           body=`{video_title} — from a {category} creator you might like`
           — metadata 拼装的保守模板, 未用 LLM
  注: doc 中 B1 模板以中文示意; 本项目视频/文案均英文, baseline 必须同语言才能
      跟 LLM output 做 paired 对比, 故 B1 按 doc 模式译成英文 (语义一一对应).

依赖: data/videos.jsonl (32) + data/personas.jsonl (13). 不依赖 Day 9 预处理产物.
CLI: python src/build_test_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

from schemas import (
    BaselinePair, PersonaRecord, TestCase, VideoRecord, make_case_id,
)

VIDEOS = Path("data/videos.jsonl")
PERSONAS = Path("data/personas.jsonl")
OUT = Path("data/test_cases.jsonl")


def derive_baseline(v: VideoRecord) -> BaselinePair:
    cat = v.category.value
    return BaselinePair(
        b0_title=f"{v.channel} just posted:",
        b0_body=v.video_title,
        b1_title=f"New video | {cat} · {v.channel}",
        b1_body=f"{v.video_title} — from a {cat} creator you might like",
    )


def main() -> None:
    videos = [VideoRecord(**json.loads(l)) for l in VIDEOS.open(encoding="utf-8")]
    personas = [PersonaRecord(**json.loads(l)) for l in PERSONAS.open(encoding="utf-8")]
    print(f"[load] {len(videos)} videos × {len(personas)} personas = {len(videos)*len(personas)} cases")

    n = 0
    with OUT.open("w", encoding="utf-8") as f:
        for v in videos:
            baseline = derive_baseline(v)  # 每视频一份, 13 persona 共享
            for p in personas:
                case = TestCase(
                    case_id=make_case_id(v.video_id, p.persona_id),
                    video=v, persona=p, baseline=baseline,
                )
                f.write(case.model_dump_json() + "\n")
                n += 1
    print(f"[OK] {n} test case → {OUT}")

    # 自检
    rows = [json.loads(l) for l in OUT.open(encoding="utf-8")]
    assert len(rows) == len(videos) * len(personas), f"行数 {len(rows)} != {len(videos)*len(personas)}"
    ids = {r["case_id"] for r in rows}
    assert len(ids) == len(rows), "case_id 有重复"
    # baseline 全非空
    for r in rows:
        b = r["baseline"]
        assert all(b[k] for k in ("b0_title", "b0_body", "b1_title", "b1_body")), f"{r['case_id']} baseline 空"
    print(f"[verify] {len(rows)} 行, case_id 唯一, baseline 全非空 ✓")


if __name__ == "__main__":
    main()
