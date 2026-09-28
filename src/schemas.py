"""push.eval 数据 schema —— 严格对齐 design-doc v0.1 § 2.4 + § 4.3.

落表用 pydantic v2, 序列化走 model_dump(mode='json'); JSONL 落盘走 jsonlines.

层次:
- VideoRecord / PersonaRecord —— 评估单元的两个输入 (trigger 是常量, 不入 schema)
- TestCase —— (video × persona) 笛卡尔积, 390 条
- GenerationOutput —— generator API 一次调用的产出
- LLMJudgeScore / HumanReviewScore —— judge / 人工评分
- OutputRow —— § 4.3 main 底表的一行, 16,380 行
- BaselineRow —— § 4.3 baseline 底表的一行, 780 行 (B0+B1 各 390)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────────────────
# 枚举常量 (对齐 design-doc 维度命名)
# ─────────────────────────────────────────────────────────

class Category(str, Enum):
    """8 个 YouTube 官方分类 (2026-05-30 抽样重构, 取代自造垂类; 见 notes).

    用官方 category, 不自造关键词 → 可复现 + 无关键词选择偏差.
    官方无 食物/美妆/健身 → Howto&Style 吸收 DIY/食物/美妆, Sports 接健身.
    """
    MUSIC = "Music"
    PETS = "Pets & Animals"
    GAMING = "Gaming"
    NEWS = "News & Politics"
    EDUCATION = "Education"
    HOWTO = "Howto & Style"
    SPORTS = "Sports"
    SCIENCE_TECH = "Science & Technology"


# 官方 videoCategoryId (美区, 由 videoCategories.list 实测确认)
CATEGORY_ID: dict[Category, str] = {
    Category.MUSIC: "10",
    Category.PETS: "15",
    Category.GAMING: "20",
    Category.NEWS: "25",
    Category.EDUCATION: "27",
    Category.HOWTO: "26",
    Category.SPORTS: "17",
    Category.SCIENCE_TECH: "28",
}


class Lifecycle(str, Enum):
    """5 档 lifecycle (§ 2.2)."""
    COLD_START = "cold-start"
    EXPLORING = "exploring"
    ENGAGED = "engaged"
    AT_RISK = "at-risk"
    DORMANT = "dormant"


class PreferenceStyle(str, Enum):
    """偏好风格 4 档 (§ 2.2). cold-start 专属 UNKNOWN."""
    UNKNOWN = "未知"
    SINGLE = "单偏好"
    NARROW = "窄偏好"
    BROAD = "宽偏好"


class VarLabel(str, Enum):
    """4 种 输入配置 (§ 2.1)."""
    A = "A"   # metadata only
    B = "B"   # + keyframes
    C = "C"   # + audio transcript
    D = "D"   # native full audio-video


class ModelName(str, Enum):
    """W2 执行子集 4 个 generator (§ 4.2)."""
    GEMINI_3_5_FLASH = "gemini-3.5-flash"
    GPT_5_5 = "gpt-5.5"
    KIMI_K2_6 = "kimi-k2.6"
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"


# ─────────────────────────────────────────────────────────
# 评估单元: Video / Persona / TestCase
# ─────────────────────────────────────────────────────────

class ContentPreference(BaseModel):
    """persona 的内容偏好 (§ 2.2)."""
    style: PreferenceStyle
    categories: list[Category] = Field(default_factory=list, description="cold-start 时为空")


class VideoRecord(BaseModel):
    """30 条 curated 视频之一 (§ 2.4 video 字段)."""
    video_id: str = Field(description="V01..V32, 内部 ID")
    youtube_id: str = Field(description="YouTube 11 字符 video ID")
    youtube_url: str
    channel: str
    category: Category = Field(description="8 官方分类之一 (唯一 spread 维度)")
    category_id: str = Field(description="官方 videoCategoryId, 如 '10'")
    publish_date: str = Field(description="ISO 日期 (描述用; 全取最近窗口, 已不做老/新分层)")
    view_count: int = Field(description="播放量 (连续协变量, 不分档)")
    duration_sec: int = Field(ge=60, le=600, description="medium 档 + 后验砍 ≤8min; 留余地 ≤10min")
    language: str = Field(default="en", description="统一英语")
    video_title: str
    video_description: str
    video_tags: list[str] = Field(default_factory=list)
    top_comments: list[str] = Field(default_factory=list, description="按点赞排前 10")

    # 4 输入配置在 schema 里只占位, 实际内容在 var_a/b/c/d 的 build 函数里组装
    # (避免 schema 里塞超长 metadata blob, 反复序列化负担大)


class PersonaRecord(BaseModel):
    """13 条 persona 之一 (§ 2.2 / § 2.4 persona 字段)."""
    persona_id: str = Field(description="P01..P13")
    lifecycle: Lifecycle
    content_preference: ContentPreference
    last_active: str = Field(description="自然语自洽时间, 如 'today' / '上次活跃 45 天前'")

    @model_validator(mode="after")
    def _check_consistency(self):
        """§ 2.2 自洽性闸: cold-start ↔ UNKNOWN; dormant ↔ 偏好已知."""
        cs = self.lifecycle == Lifecycle.COLD_START
        unknown = self.content_preference.style == PreferenceStyle.UNKNOWN
        if cs != unknown:
            raise ValueError(
                f"persona {self.persona_id}: cold-start 与 UNKNOWN 必须同进同出, "
                f"got lifecycle={self.lifecycle}, style={self.content_preference.style}"
            )
        # cold-start 偏好分类必须为空
        if cs and self.content_preference.categories:
            raise ValueError(f"{self.persona_id}: cold-start 偏好分类必须为空")
        if not cs and not self.content_preference.categories:
            raise ValueError(f"{self.persona_id}: 非 cold-start 必须有偏好分类")
        return self


class BaselinePair(BaseModel):
    """单条 case 配的 2 层 baseline (§ 2.4 / § 4.3 baseline 底表派生源)."""
    b0_title: str
    b0_body: str
    b1_title: str
    b1_body: str


class TestCase(BaseModel):
    """单条评估单元 (§ 2.4); 416 条 = 32 video × 13 persona."""
    case_id: str = Field(description="{video_id}_{persona_id}, 如 V07_P05")
    video: VideoRecord
    persona: PersonaRecord
    trigger: str = Field(
        default="推荐 × 视频 × 陌生作者 (§ 2.3 固定 setting)",
        description="所有 case 共享常量",
    )
    baseline: BaselinePair


# ─────────────────────────────────────────────────────────
# Generator 调用产出
# ─────────────────────────────────────────────────────────

class PreprocessMeta(BaseModel):
    """§ 4.3 preprocess_meta 字段 — 兑现 § 2.1 输入配置 caveat.

    var B 用 keyframe_count / scene_count; var C 用 transcript_*; var D 二者俱可用.
    v0.1 落表不切片分析, v0.2 候选.
    """
    keyframe_count: int | None = None
    scene_count: int | None = None
    transcript_language: str | None = Field(default=None, description="Whisper 自动检测语种 (非 en = 音频非英语)")
    transcript_word_count: int | None = None
    transcript_confidence: float | None = Field(default=None, description="Whisper 自带 logprob 均值")
    audio_speech_ratio: float | None = Field(default=None, description="语音段占总时长比例")
    video_has_text_overlay: bool | None = None


class GenerationOutput(BaseModel):
    """generator 单次 API 调用产出 (落入 OutputRow 的 output_* + tokens_* + latency_ms + cost_usd)."""
    title: str
    body: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    model_version: str = Field(description="API 返回的精确 model ID, 含 vendor 端 build 标识")
    raw_response: dict[str, Any] | None = Field(default=None, description="保留 raw, debug 用")


# ─────────────────────────────────────────────────────────
# Judge / 人工评分
# ─────────────────────────────────────────────────────────

class SafetyScore(BaseModel):
    """4 类安全 binary (§ 4.1 合规门 维度)."""
    misleading: bool        # 误导失真
    manipulative: bool      # 心理操纵
    harmful: bool           # 有害内容
    privacy: bool           # 隐私泄露

    def any_violation(self) -> bool:
        """任一类违规 = 不合规."""
        return self.misleading or self.manipulative or self.harmful or self.privacy


class EffectScore(BaseModel):
    """9 维效果 1-5 (§ 4.1 效果侧). 偏好匹配 对 cold-start 为 None."""
    # 文案质量 4 维
    readability: int = Field(ge=1, le=5)         # 可读性
    video_relevance: int = Field(ge=1, le=5)     # 视频相关性
    content_fidelity: int = Field(ge=1, le=5)    # 内容忠实度
    expressiveness: int = Field(ge=1, le=5)      # 表达力
    # 推送体验 5 维
    naturalness: int = Field(ge=1, le=5)         # 自然度
    expectation_alignment: int = Field(ge=1, le=5)  # 预期一致性
    preference_match: int | None = Field(default=None, ge=1, le=5)  # 偏好匹配 (cold-start N/A)
    tone_fit: int = Field(ge=1, le=5)            # 语气适配 (按 lifecycle 4 套 anchor)
    push_value: int = Field(ge=1, le=5)          # 打扰价值

    def equal_weight_mean(self) -> float:
        """9 维等权 (cold-start 偏好匹配 N/A 时降为 8 维等权; § 4.1)."""
        vals = [
            self.readability, self.video_relevance, self.content_fidelity,
            self.expressiveness, self.naturalness, self.expectation_alignment,
            self.tone_fit, self.push_value,
        ]
        if self.preference_match is not None:
            vals.append(self.preference_match)
        return sum(vals) / len(vals)


class LLMJudgeScore(BaseModel):
    """单 judge 模型对单条 output 的评分."""
    judge_model: ModelName | str
    safety: SafetyScore
    effect: EffectScore


class HumanReviewScore(BaseModel):
    """人工抽检 (§ 4.3 Step 4); 仅 sampled_for_human=True 的 output 有."""
    safety: SafetyScore
    effect: EffectScore
    length_compliant: bool
    comment: str = ""


# ─────────────────────────────────────────────────────────
# 底表 row (§ 4.3 数据落表)
# ─────────────────────────────────────────────────────────

class OutputRow(BaseModel):
    """main 底表一行 (§ 4.3); 16,380 行 = 5,460 cell × 3 重复.

    分阶段填充:
      Day 11-14 generator: cell/run/output_*/tokens/latency/cost/length_compliant/preprocess_meta
      W3 judge:            safety_judge*/effect_judge*/auto_evidence
      W3 sampling:         sampled_for_human/human_*
    """
    # 上下文
    cell_id: str = Field(description="{model}|{var}|{case_id}, 唯一标识 cell")
    case_id: str
    run_id: int = Field(ge=1, le=3, description="同 cell 第几次重复")
    model: ModelName | str
    var_label: VarLabel
    video_id: str
    persona_id: str
    generated_at: datetime

    # generator 产出
    output_title: str
    output_body: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    model_version: str
    length_compliant: bool = Field(description="auto: title ≤ 50 且 body ≤ 150")

    # preprocess 质量元数据 (兑现 § 2.1 caveat)
    preprocess_meta: PreprocessMeta = Field(default_factory=PreprocessMeta)

    # judge 评分 (W3 阶段填; 2 judges 独立)
    judge1: LLMJudgeScore | None = None
    judge2: LLMJudgeScore | None = None
    auto_evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="拼写错位置 / 语法错位置 / 套路词命中清单 — 输入给 judge prompt, 不直接计分",
    )

    # 人工抽检 (W3 阶段填; 仅 2-3% 行有)
    sampled_for_human: bool = False
    human_review: HumanReviewScore | None = None


class BaselineRow(BaseModel):
    """baseline 底表一行 (§ 4.3); 780 行 = B0+B1 各 390. 跟 main 表 case_id join."""
    case_id: str
    baseline_kind: str = Field(pattern="^(b0|b1)$", description="b0=现状 / b1=规则启发式")
    baseline_title: str
    baseline_body: str
    length_compliant: bool

    # judge 评分 (同 main schema)
    judge1: LLMJudgeScore | None = None
    judge2: LLMJudgeScore | None = None


# ─────────────────────────────────────────────────────────
# 辅助: cell_id 构造 (统一格式, 防散落各处)
# ─────────────────────────────────────────────────────────

def make_cell_id(model: ModelName | str, var: VarLabel, case_id: str) -> str:
    model_str = model.value if isinstance(model, ModelName) else model
    return f"{model_str}|{var.value}|{case_id}"


def make_case_id(video_id: str, persona_id: str) -> str:
    return f"{video_id}_{persona_id}"
