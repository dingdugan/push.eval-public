"""按新 8 官方分类硬码 13 个 persona, 落 data/personas.jsonl.

2026-05-30 抽样重构: 偏好维度从自造垂类 → 8 个 YouTube 官方分类.
旧 食物/美妆→Howto&Style, 旧 健身→Sports, 新增 Science & Technology.

偏好风格仍是 单(1)/窄(2)/宽(4); cold-start 唯一 UNKNOWN(0).
8 分类每个覆盖 3-4 次 (总 28 个偏好槽 = 4×单1 + 4×窄2 + 4×宽4... 实为
4 lifecycle 组 × (1+2+4)=7 = 28), 经手工配平.

last_active 按 lifecycle 自洽 (schema validator 兜底).
"""

from __future__ import annotations

from pathlib import Path

from schemas import (
    Category,
    ContentPreference,
    Lifecycle,
    PersonaRecord,
    PreferenceStyle,
)

C = Category
PS = PreferenceStyle
LC = Lifecycle


PERSONAS: list[PersonaRecord] = [
    # P1: cold-start (唯一, 偏好未知)
    PersonaRecord(
        persona_id="P1",
        lifecycle=LC.COLD_START,
        content_preference=ContentPreference(style=PS.UNKNOWN, categories=[]),
        last_active="today",
    ),
    # P2-P4: exploring × {单/窄/宽}
    PersonaRecord(
        persona_id="P2",
        lifecycle=LC.EXPLORING,
        content_preference=ContentPreference(style=PS.SINGLE, categories=[C.SPORTS]),
        last_active="今天 (首次活跃 8 天前)",
    ),
    PersonaRecord(
        persona_id="P3",
        lifecycle=LC.EXPLORING,
        content_preference=ContentPreference(style=PS.NARROW, categories=[C.EDUCATION, C.MUSIC]),
        last_active="昨天 (首次活跃 15 天前)",
    ),
    PersonaRecord(
        persona_id="P4",
        lifecycle=LC.EXPLORING,
        content_preference=ContentPreference(
            style=PS.BROAD,
            categories=[C.SPORTS, C.EDUCATION, C.HOWTO, C.NEWS],
        ),
        last_active="今天 (首次活跃 22 天前)",
    ),
    # P5-P7: engaged × {单/窄/宽}
    PersonaRecord(
        persona_id="P5",
        lifecycle=LC.ENGAGED,
        content_preference=ContentPreference(style=PS.SINGLE, categories=[C.HOWTO]),
        last_active="today",
    ),
    PersonaRecord(
        persona_id="P6",
        lifecycle=LC.ENGAGED,
        content_preference=ContentPreference(style=PS.NARROW, categories=[C.HOWTO, C.SPORTS]),
        last_active="today",
    ),
    PersonaRecord(
        persona_id="P7",
        lifecycle=LC.ENGAGED,
        content_preference=ContentPreference(
            style=PS.BROAD,
            categories=[C.PETS, C.EDUCATION, C.SCIENCE_TECH, C.MUSIC],
        ),
        last_active="昨天",
    ),
    # P8-P10: at-risk × {单/窄/宽}
    PersonaRecord(
        persona_id="P8",
        lifecycle=LC.AT_RISK,
        content_preference=ContentPreference(style=PS.SINGLE, categories=[C.GAMING]),
        last_active="5 天前",
    ),
    PersonaRecord(
        persona_id="P9",
        lifecycle=LC.AT_RISK,
        content_preference=ContentPreference(style=PS.NARROW, categories=[C.PETS, C.SCIENCE_TECH]),
        last_active="8 天前",
    ),
    PersonaRecord(
        persona_id="P10",
        lifecycle=LC.AT_RISK,
        content_preference=ContentPreference(
            style=PS.BROAD,
            categories=[C.GAMING, C.MUSIC, C.NEWS, C.SCIENCE_TECH],
        ),
        last_active="11 天前",
    ),
    # P11-P13: dormant × {单/窄/宽}
    PersonaRecord(
        persona_id="P11",
        lifecycle=LC.DORMANT,
        content_preference=ContentPreference(style=PS.SINGLE, categories=[C.NEWS]),
        last_active="35 天前",
    ),
    PersonaRecord(
        persona_id="P12",
        lifecycle=LC.DORMANT,
        content_preference=ContentPreference(style=PS.NARROW, categories=[C.GAMING, C.MUSIC]),
        last_active="60 天前",
    ),
    PersonaRecord(
        persona_id="P13",
        lifecycle=LC.DORMANT,
        content_preference=ContentPreference(
            style=PS.BROAD,
            categories=[C.PETS, C.GAMING, C.SPORTS, C.EDUCATION],
        ),
        last_active="120 天前",
    ),
]


def _verify_coverage() -> None:
    """自查: 13 persona 可区分 + 8 官方分类全覆盖 + lifecycle 5 档全覆盖."""
    assert len(PERSONAS) == 13, f"persona 数错: {len(PERSONAS)}"
    ids = [p.persona_id for p in PERSONAS]
    assert len(set(ids)) == 13, f"persona_id 重复: {ids}"

    cycles = {p.lifecycle for p in PERSONAS}
    assert cycles == set(Lifecycle), f"lifecycle 未全覆盖: {cycles}"

    covered = set()
    for p in PERSONAS:
        covered.update(p.content_preference.categories)
    missing = set(Category) - covered
    assert not missing, f"分类未全覆盖: {missing}"

    from collections import Counter
    counts = Counter(c for p in PERSONAS for c in p.content_preference.categories)
    for cat in Category:
        n = counts[cat]
        assert 3 <= n <= 5, f"分类 {cat.value} 覆盖 {n} 次, 期望 3-5"

    # 偏好风格 → 分类数 一致性
    for p in PERSONAS:
        n = len(p.content_preference.categories)
        style = p.content_preference.style
        expect = {PS.UNKNOWN: 0, PS.SINGLE: 1, PS.NARROW: 2, PS.BROAD: 4}[style]
        assert n == expect, f"{p.persona_id}: {style} 应 {expect} 个分类, 实 {n}"


def main() -> None:
    _verify_coverage()
    out_path = Path("data/personas.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for p in PERSONAS:
            f.write(p.model_dump_json() + "\n")
    print(f"[OK] 落盘 {len(PERSONAS)} persona → {out_path}")

    from collections import Counter
    print("    lifecycle 分布:")
    for lc, n in sorted(Counter(p.lifecycle.value for p in PERSONAS).items()):
        print(f"      {lc}: {n}")
    print("    分类覆盖次数:")
    counts = Counter(c.value for p in PERSONAS for c in p.content_preference.categories)
    for cat in Category:
        print(f"      {cat.value:<22}: {counts[cat.value]}")


if __name__ == "__main__":
    main()
