# W1 Skeleton — Eval Design Doc

> Working skeleton for Week 1. Each section lists its goal, the core questions to answer, the concrete deliverables, and an estimated page count.
>
> Target total: 12–18 pages of pure prose + tables. No code in this document.

---

## Section 1: Background, Goals & Research Method Framework (Day 1, 1.5–2 pages) — ✅ 已成稿

**Goal**: 务实陈述 (a) 这是什么项目、(b) 解决什么问题、对谁有价值、scope 边界、(c) 研究方法的整体框架——为 Sections 2-6 提供蓝图。

**写作取向**：本节不写 "why now / why hard" 这类 retrospective 或 explainer 措辞。所有难点在 Sections 2-6 各组件的「选型原因」段自然出现，不集中堆在 Section 1。

**Sections 2-6 内部 shape**：4 段式（**作用 / 方案选型 / 选型原因 / 执行方案**）作为 default template。当内容自然不符合时（某子节没有显式选型决策、或选型逻辑已在前序章节交代等），允许变形——压缩为「参数总览表 + 关键决策理由 + 执行细节」或其他更贴合内容的结构。「选型原因」相关段排序约定保留：先业务 / 用户角度，再成本角度。

§ 1.1–1.4 已全部成稿，见 `docs/eval-design-doc.md`：
- § 1.1 一句话 doc 目的
- § 1.2 背景（业务背景 + bad push 反例 + 技术背景）
- § 1.3 eval 目的、价值与 scope（含 trigger 固定为「推荐 / 视频 / 陌生」）
- § 1.4 研究方法框架（pipeline 图 `(video, persona, trigger) → model → (title, body)` + 5 组件表）

---

## Section 2: 评估单元 (Day 1–3, ~3 pages)

**Goal**: 定义评估单元的 schema、数据来源、处理 pipeline。一个 case = `(video, persona, trigger) → (title, body)`。

**Internal shape (final doc)**: 默认遵循 Section 1 末尾的 4-段式 template，按内容需要变形。

---

### § 2.1 Video 来源 + 处理 pipeline — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- 视频来源 = YouTube（平台对比表论证：YouTube / Facebook / Instagram / TikTok / Snapchat 五平台中唯一三维全达标）
- 30 条；主分层 = 垂类（8 个）；其他维度统一（英语 / 1–5 min / view ≥ 1M）
- 4 个变量 A/B/C/D，用「信息 × 成本」2×2 定位；核心可测假设 = C 是否支配 B

---

### § 2.2 Persona 来源 + 处理 pipeline — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- 13 个合成 persona：cold-start ×1（偏好「未知」）+ 4 lifecycle × 3 偏好档（单/窄/宽）
- lifecycle 5 档由 2 事实判定（首次活跃时间 + 近 30 天活跃天数），对「曾活跃用户基」严格 MECE
- 内容偏好 4 档：未知 / 单（1 垂类）/ 窄（2）/ 宽（4）
- persona 记录 = `{ lifecycle, 内容偏好, 最近活跃时间 }`，偏好显式给出、不要求模型反推
- 设计约束 flag 给 § 4.1：「个性化」评分须按 lifecycle 分情况

---

### § 2.3 Trigger 设定 — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- trigger 是评估单元第三输入；多维空间，本 eval 固定为单一切片「推荐 × 视频 × 陌生」
- 维度：内容类型（推荐/通知/营销）/ 内容体裁（视频/文本）/ 作者-用户关系（关注/好友/陌生）；触发时间不建模
- 选型理由：该格子最需要靠模型优化文案——推荐无真实事件可呈现、陌生无社交背书、视频→文本比文本→文本更难
- 策略：先攻最难格子，有效再向其他切片复用
- trigger 固定 → 不增 test case 维度，仍为 30 × 13 = 390

---

### § 2.4 评估单元 schema — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- test case 记录 = `{ case_id, video{video_id, vertical, var_A/B/C/D}, persona{...}, trigger }`，共 390 条
- 记录只含输入；模型输出与评分分别在 § 3 / § 4 产生
- 偏好匹配状态为派生分析维度（不入 schema），§ 7 结果分析用
- 执行：批量生成 390 条 + 人工抽查 5 条

---

## Section 3: 评估对象 — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- 9 个模型 = 每个头部 frontier 厂商取最强旗舰（6 闭源 + 3 开源）：GPT-5.5 / Claude Opus 4.7 / Gemini 3.5 Flash / Grok 4.20 / Doubao Seed 2.0 Pro / Llama 4 Maverick / Qwen3.7-Max / DeepSeek V4-Pro / Kimi K2.6
- 评估对象 = 9 模型 × 4 变量矩阵
- 9 模型全支持 var A/B/C；var D（原生视频）2026-Q2 实际覆盖：Gemini 3.5 Flash / Doubao Seed 2.0 Pro / Kimi K2.6 三家明确稳定 ✅，Qwen3.7-Max 为 ⚠️ pilot verify，其余 5 家 ❌
- 未纳入 MiniMax（纯文本）/ Zhipu GLM（tier-1.5）
- 9 为完整设计；执行子集见 § 4.2

---

## Section 4: 评估方法 (Day 4–5, ~4–5 pages，**整 doc 的核心**)

**Goal**: 定义在「评估对象」空间上的打分系统与实验控制。

**Internal shape (final doc)**: 默认遵循 Section 1 末尾的 4-段式 template，按内容需要变形。

---

### § 4.1 评估指标 — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- 5 板块结构：总体架构 / 安全侧 / 效果侧 / 成本与耗时侧 / 汇总
- 总体架构：指标 = 安全（gate）+ 效果 / 成本 / 耗时（3 目标 Pareto）；安全与效果不可通约，必为门
- 安全侧：4 类失败（误导失真 / 心理操纵 / 有害内容 / 隐私泄露）；LLM-judge 二元判定；per-model 安全率作 gate，阈值 95–98%（精度受 1/390 ≈ 0.26% 限）
- 效果侧：8 维度（3 大组 × 各维度子项带 ladder / severity 标注）；LLM-judge 主导 + auto 辅助拼写/语法/套路词；长度合规 binary pass=5/fail=1
- 成本与耗时侧：成本（美金）+ 耗时（毫秒），auto 测，公平 controls
- 汇总：底表（per-push 全量）→ 4 指标聚合 → 安全门 → 3 目标 Pareto；1–5 打分框架 ladder/severity 双套，5 行 anchor
- LLM-judge 池：Gemini 3.5 Flash / Doubao Seed 2.0 Pro / Kimi K2.6（3 家全部明确 ✅ var D）；硬约束=始终用完整视频信息作参考；避免自评

---

### § 4.2 实验设计 — ✅ 已成稿

已写入 `docs/eval-design-doc.md`。要点：
- 完整设计 = 「模型 × 变量 × test case × 重复」笛卡尔积；9 × 4 × 390 × 3 = 42,120 次调用
- 执行子集 4 模型（Gemini 3.5 Flash / GPT-5.5 / Kimi K2.6 / DeepSeek V4-Pro）：2 闭源 + 2 开源；2 个 var D ✅ 稳定锚点（Gemini 闭源 + Kimi 开源 — 跨生态交叉验证 ablation 结论）+ 2 个 var D ❌（GPT-5.5 / DeepSeek，A/B/C 段）
- 执行子集规模：16,380 次调用
- modality 是唯一可消融的输入维度（persona 已网格全覆盖、trigger 已固定）
- 笛卡尔而非采样：保切片分析样本量 + 不进一步劣化 1/390 精度下限
- 重复 3 次：paired comparison 最小重复数 + 性价比临界点
- 控制变量：temperature = 0.7 / 同 prompt 模板 / 无特殊 system prompt / vendor 缓存关闭 / 同时段同并发
- 执行顺序：pilot（Gemini 4 变量 × 5 case = 20 次调用 + Kimi var D × 3 case = 3 次 verify）→ 全量
- 预算：~$325（不 batch）或 ~$160（batch 5 折）+ 数十小时；GPT-5.5 和 Gemini 3.5 Flash 是主要成本项；超预算缩减优先级 = 保模型数 > 保 var 覆盖 > 保 case 数 > 保重复数

> **注**：本 § 4.2 摘要按 step 1 最小改名替换写就；§ 4.2 整段内容（子集大小、Claude 是否保留、控制变量细则、预算颗粒度等）由 step 2 重写决定，届时本摘要同步更新。

---

### § 4.3 评估流程 (Day 5 morning, ~1 page)

**作用**：定义如何把模型输出转化为分数。

**方案选型**：三层组合——自动指标 + LLM-as-judge + 10% 人工抽检。

**选型原因**：
- 三层互相补充，单一来源都有 bias
- 10% 人工 = 成本可控同时校准 LLM-judge

**执行方案**：
- 自动指标：长度 / cost / latency / embedding-based novelty
- LLM-as-judge 设计：≥2 个 judge 模型 / position bias / prompt anchoring / 一致性测试
- 人工抽检 protocol（blind to model + 变量）

---

### § 4.4 统计方法 (Day 5 afternoon, ~0.5 page)

**作用**：定义如何从 raw 分数得到可信结论。

**方案选型**：3 次重复 + 显著性检验 + Pareto front 分析。

**选型原因**：
- 3 次重复 = 成本 / 可信度平衡
- Paired comparison + 多重检验校正 = 声称"模型 X 在维度 Z 上击败 Y"需要的统计严谨度

**执行方案**：
- Temperature = 0.7 / fixed seed where supported
- 报告 mean ± std + 可选 CI
- Paired comparison with multiple-test correction

---

> § 5 决策框架 + § 6 思考与未来工作 已从 W1 框架 doc 移出——它们依赖实际 eval 数据，将在执行后另起 doc 产出（含 结果分析 / 决策表 / 完整反思与未来工作）。本 W1 doc 边界结束于 § 4 评估方法。

---

## Day-by-Day Checklist (W1)

- [x] Day 1: Section 1 complete + Section 2 started（§ 2.1 已成稿）
- [x] Day 2: Section 2.2 (persona) complete
- [x] Day 3: Section 2.3 (trigger) + Section 2 schema + Section 3 (评估对象) complete
- [x] Day 4: Section 4.1 评估指标 complete ← **最关键**
- [ ] Day 5: Section 4.2 / 4.3 / 4.4 complete
- [ ] Day 6: 全 doc 通读 + golden-line self-check pass
- [ ] Day 7: Reviewer feedback absorbed + 终稿
