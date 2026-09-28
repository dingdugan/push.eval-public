"""每个官方 category 的 5 个真人感英文 query (LLM 生成), OR 拼成 q + category 硬过滤.

设计: query 模拟真实用户会在 YouTube 搜的口吻 (不是干巴巴 "sports"),
用 | OR 撒网扩召回 + 降单词偏差; videoCategoryId 做硬分类约束.
英文 (跟 relevanceLanguage=en + 英语视频约束一致).
"""

from schemas import Category

# 每类 5 个 query; 真跑时 "|".join(...) 成单个 q
CATEGORY_QUERIES: dict[Category, list[str]] = {
    Category.MUSIC: [
        "official music video new",
        "live performance song",
        "song cover acoustic",
        "music video 2026",
        "new single release artist",
    ],
    Category.PETS: [
        "funny dog moments",
        "cat doing funny things",
        "cute puppy compilation",
        "rescue animal story",
        "golden retriever daily life",
    ],
    Category.GAMING: [
        "best gaming moments",
        "game walkthrough gameplay",
        "new game review 2026",
        "speedrun world record",
        "ranked match highlights",
    ],
    Category.NEWS: [
        "breaking news today explained",
        "latest world news analysis",
        "political news update",
        "what happened this week news",
        "news report coverage",
    ],
    Category.EDUCATION: [
        "how does it work explained",
        "study tips that actually work",
        "learn something new in minutes",
        "history explained simply",
        "science concept explainer",
    ],
    Category.HOWTO: [
        "easy recipe step by step",
        "makeup tutorial look",
        "skincare routine guide",
        "diy home project tutorial",
        "how to cook quick meal",
    ],
    Category.SPORTS: [
        "NBA best players highlights",
        "latest football match hot cut",
        "workout routine at home",
        "gym training motivation",
        "best goals of the season",
    ],
    Category.SCIENCE_TECH: [
        "new gadget review 2026",
        "AI explained for beginners",
        "space discovery news",
        "tech unboxing first look",
        "how technology works",
    ],
}


def build_q(cat: Category) -> str:
    """5 query OR 拼成单个 q 字符串."""
    return "|".join(CATEGORY_QUERIES[cat])
