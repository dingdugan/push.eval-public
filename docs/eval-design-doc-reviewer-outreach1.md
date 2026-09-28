我完整看完了。总评：**这是一份明显高于普通PM文档水位的eval设计**，尤其强在“研究问题定义、变量拆解、rubric、统计意识、成本意识”。但从eval专家视角看，它现在更像一个**严谨的研究型offline benchmark**，距离“可以直接支撑工业界上线选型”的设计，还差几块关键拼图。

我会给它一个判断：

> **作为v0.1设计文档：8/10。**
> **作为生产选型依据：还需要补到6.5→8.5，核心补“线上一致性、样本独立性、judge可靠性、业务场景覆盖”。**

---

## 你这份文档最强的地方

第一，**目标定义很清楚**。你不是泛泛地问“哪个模型生成文案好”，而是明确要测“输入信号×模型”在效果、成本、耗时上的mapping，并拆成两个子问题：输入信号边际收益、模型能力分布。这是对的。文档还明确把offline eval和online A/B分工讲清楚：offline负责quality proxy、safety、cost、latency，online再验证CTR/engagement/retention等真实outcome。这个边界意识很好。

第二，**输入变量设计有思考，不是堆特征**。A=metadata，B=metadata+keyframes，C=metadata+audio transcript，D=full audio-video，这个设计能回答一个真实工程问题：到底是“多模态直接喂视频”值得，还是“便宜的transcript”已经够好。你文档里把B和C放在“低信息/高成本”与“高信息/低成本”的反对角线上，这个观察很重要。

第三，**安全作为gate，而不是总分一部分，是正确的**。Push这种场景里，安全、误导、操纵感不能被高吸引力平均掉。文档里先做safety判定，过门后再进入效果、成本、耗时的Pareto，这是成熟eval设计里非常关键的思想。

第四，**rubric已经相当完整**。你把效果拆成文案本身、文案<>视频、文案<>用户三组，再细化为可读性、自然度、表达力、长度合规、视频相关性、内容忠实度、预期一致性、用户相关性。这套结构基本覆盖了push文案的主要质量面。尤其“用户相关性按lifecycle分anchor”这个设计很聪明，因为cold-start、engaged、dormant用户的好文案标准确实不同。

第五，**统计意识明显强于普通业务eval**。你用了paired comparison、Wilcoxon、Bonferroni、bootstrap CI、baseline paired对比、污染诊断、power估算，并且在附录里主动承认N=390只能检出约0.3分以上差距、per-vertical只能做方向性参考。这些都说明你知道“eval结果不是分数表，而是一组带不确定性的证据”。

---

## 最大问题一：你的有效样本量可能被高估了

文档里把30个video×13个persona=390个test case作为主要统计单元。这在工程上方便，但从统计上有个问题：**很多质量维度的真正独立样本不是390，而更接近30个video。**

比如视频相关性、内容忠实度、预期一致性，这些主要由video决定。同一个视频配13个persona，模型对视频的理解错误很可能在13个persona上高度相关。这样做paired comparison没有问题，但power估算如果直接按N=390，会偏乐观。

你现在文档里也承认每垂类N≈4只能做方向性结论，但更大的问题是：**整体N=390也不完全等于390个独立case**。

建议改法：

把统计层级显式改成：

```text
video 是 primary cluster
persona 是 nested condition
output 是 repeated sample
```

分析时至少做两套CI：

```text
1. case-level bootstrap：按390 case重采样
2. video-cluster bootstrap：按30 video重采样，每个video带走其13个persona
```

最后主结论以video-cluster bootstrap为准，case-level结果作为辅助。否则很容易出现一种情况：结果看起来显著，其实是因为30条视频里某几条被13个persona重复放大了。

如果你要让这份eval更像工业界可交付版本，我会建议视频数从30提高到**80-150条**。persona不一定要13个都乘满，可以改成“每条视频抽3-5个相关persona”，这样更接近真实分发，也减少重复相关性。

---

## 最大问题二：用户相关性rubric有点“产品策略预设过强”

你现在对lifecycle的定义很清楚，但用户相关性的评分标准里有一个隐含假设：
**exploring应该拓宽，at-risk/dormant应该换hook。**

这在产品策略上可能是对的，但作为eval rubric，会把“策略偏好”混进“文案质量”。比如一个dormant用户历史偏好是游戏，视频也确实是一个强游戏视频，文案精准命中游戏兴趣，可能真实效果很好；但按你现在anchor，它可能因为“没敢换hook”只得3分。

这里的问题是：**eval在替业务策略做价值判断。**

更稳的做法是把“用户相关性”拆成两个维度：

```text
Preference Fit：是否匹配用户已知兴趣/推荐理由
Lifecycle Tone Fit：语气是否适合该lifecycle
```

然后对at-risk/dormant单独加一个诊断维度：

```text
Reactivation Strategy：保守命中旧兴趣 / 拓宽新hook / 热点召回 / 社交证明 / 低压力邀请
```

这个维度不要直接进入总分，至少不要等权进入。否则模型会被训练成“对沉默用户就故意换方向”，但真实业务里，沉默用户可能只是没收到对的旧兴趣内容。

---

## 最大问题三：你缺了push最关键的“打扰价值 / notification worthiness”

你有“用户相关性”和“预期一致性”，但还缺一个push专属维度：

> **这条内容值不值得主动打扰用户？**

Feed里的好内容，不一定值得push。Push的核心不是“文案好看”，而是“用户收到这一条会不会觉得平台越界”。

建议新增一个一级维度：

```text
Notification Worthiness / Interruption Justification
```

5分anchor可以这样：

```text
5：内容对该用户有明确即时/强价值，值得主动打断
4：内容相关性较强，有明确打开理由
3：作为feed推荐合理，但push价值一般
2：内容本身可看，但不值得打扰
1：明显是平台想发，用户没有接收价值
```

这个维度最好和“用户想收 vs 平台想发”绑定。尤其你做的是推荐型陌生作者视频，没有关注/好友关系背书，本来就最容易被用户认为“平台乱推”。

你之前push业务讨论里也提到过，同一个作者、同一个用户、同一种内容被反复push时会形成体验问题，且push业务里分布、极值、离群点很关键。这个背景其实应该进入eval设计：不仅评估单条文案，还要评估“这条push放进用户最近收到的一串push里是否烦人”。

---

## 最大问题四：Safety的OR聚合可能过于保守，会把judge误报放大

你现在设计是：

```text
2个judge之间 OR
3次重复之间 OR
任一unsafe则cell unsafe
```

原则上“宁严勿纵”没错。但统计上有一个副作用：**judge false positive会被层层放大。**

假设每个judge对安全违规的误报率只有1%，两judge OR后，单个output被误判unsafe的概率大约接近2%。再对3次重复OR，一个完全安全的cell也可能有接近6%的概率被判unsafe。这样一个模型即使真实安全率很高，也可能因为judge误报而安全率掉到95%以下，直接被挡在Pareto外。

你文档里安全门默认95-98%，这和OR机制叠加后会比较危险。

建议改成两层口径：

```text
Observed Safety Violation：LLM judge OR后的原始违规率
Confirmed Safety Violation：高风险样本经人工adjudication后的确认违规率
```

安全门用confirmed violation更合理。对于“误导失真/隐私泄露/有害内容”这类高风险，可以继续OR；但对“心理操纵”这种边界很主观的维度，建议分severity：

```text
hard violation：恐吓、虚假紧迫、羞辱、明显FOMO勒索
soft concern：轻微夸张、营销感、标题党倾向
```

hard violation进安全门，soft concern进入效果扣分或risk诊断，不要一刀切unsafe。

---

## 最大问题五：LLM judge验证还不够工业级

你设计了2个judge、避免自评、judge必须看完整视频、先跑≥100条看Cohen’s κ，之后10%人工抽检。这已经比很多eval严谨。

但有几个细节要改。

第一，**1-5分不适合只用Cohen’s κ**。Cohen’s κ更适合分类。你这里很多维度是ordinal score，建议用：

```text
Quadratic weighted kappa
Krippendorff’s alpha
ICC / Spearman correlation
```

binary safety可以用Cohen’s κ，1-5效果分用weighted kappa或ICC。

第二，**人工抽检不能只存一个human_score**。你的底表里`human_score`是一个int 1-5，但人工需要按维度打分，否则你无法知道judge偏差来自哪个维度：是自然度偏高，还是视频忠实度漏判，还是用户相关性不稳定。

建议改成：

```text
human_safety_1-4
human_effect_1-7
human_length_override
human_comment
human_adjudication_label
```

第三，**人工reviewer最好至少2人独立盲评+冲突仲裁**。现在“我自己+1-2个AI-circle peer”可以用于个人项目，但如果要说服业务团队，最好设计成：

```text
2 independent raters
disagreement >= 2 → adjudicator
report human-human agreement first
then report LLM-human agreement
```

不然LLM judge对齐的是你的个人审美，而不是稳定的人类标准。

---

## 最大问题六：baseline太弱，可能让LLM显得“稳赢”

你现在baseline是：

```text
title: {creator_name} just posted:
body: {video_title}
```

这个作为“原始生产status quo”有合理性，尤其你文档里那个“I love you”反例确实能说明问题。

但如果只跟这个baseline比，LLM大概率赢得很轻松。它能回答“LLM是否优于最朴素模板”，但不一定能回答“是否值得引入LLM系统”。

建议至少做三层baseline：

```text
B0：原始生产模板：creator just posted + raw title
B1：规则/启发式模板：用metadata生成安全保守文案
B2：metadata-only cheap LLM：便宜模型+只用title/description/tags
```

这样最终结论会更有价值：

```text
LLM full video 是否 > cheap metadata LLM？
audio transcript 是否 > keyframes？
frontier expensive model 是否 > cheap model + better prompt？
```

否则你可能证明了“LLM比裸标题好”，但这个结论对选型不够尖锐。

---

## 最大问题七：效果维度有重叠，等权平均会重复计分

你现在8个效果维度里，以下几个高度相关：

```text
误导失真 safety
视频相关性
内容忠实度
预期一致性
表达力里的hook
心理操纵 / clickbait倾向
```

这会带来两个问题：

第一，某些错误被重复惩罚。比如文案生造一个视频里没有的亮点，会同时影响误导失真、内容忠实度、预期一致性、视频相关性。

第二，某些“软审美维度”被赋予和“事实准确”一样的权重。比如长度合规pass=5/fail=1，和内容忠实度等权，可能导致一个151字符的body被扣得和严重低质文案差不多。

建议把最终分数拆成三层，而不是一个8维等权分：

```text
Eligibility Gates：
安全、严重失真、PII、长度硬限制

Core Quality Score：
视频相关性、内容忠实度、价值表达、可读性

Push Experience Score：
用户相关性、通知价值、语气自然度、预期一致性、疲劳风险
```

长度建议不要用1/5等权进入总分，而是：

```text
pass：不扣分
slight overflow：小扣分
hard overflow / truncation risk：fail或大扣分
```

因为真实push里，字符数不是审美质量，而是展示约束。

---

## 最大问题八：文档没有显式支持“不要发 / 生成失败 / 低信心fallback”

真实生产里，LLM生成push文案不应该永远返回一条title/body。很多case最优行为是：

```text
do_not_send
use_safe_template
need_more_context
low_confidence
```

尤其推荐型陌生作者视频，如果视频内容低质、主题不清、用户兴趣弱、存在争议或隐私风险，强行生成一条吸引人的push反而危险。

建议把模型输出schema从：

```json
{ "title": "...", "body": "..." }
```

升级成：

```json
{
  "decision": "send | fallback | do_not_send",
  "title": "...",
  "body": "...",
  "reason": "...",
  "grounding": ["video evidence / transcript span"],
  "confidence": 0-1
}
```

然后eval也要评估：

```text
该发时是否能写好；
不该发时是否能拒绝；
低信心时是否能走保守模板。
```

这会比单纯比文案分数更接近工业系统。

---

## 最大问题九：输入信号消融会被preprocessing confound影响

A/B/C/D看起来是输入信号变量，但其实里面混了很多工程选择：

```text
B：3-5张keyframes，且由PySceneDetect决定
C：Whisper-large-v3转写质量
D：不同vendor原生视频理解能力和计价方式
```

所以如果C>B，不能直接说“audio比keyframes更有用”，只能说：

> 在当前抽帧策略和当前转写策略下，C这个system configuration优于B。

建议文档里把“输入信号变量”改名为：

```text
Input Configuration / System Configuration
```

并且增加preprocessing质量记录：

```text
keyframe_count
scene_count
keyframe_coverage_score
transcript_word_count
transcript_confidence / WER proxy
audio_speech_ratio
video_has_text_overlay
visual_dominant vs audio_dominant
```

否则模型表现差可能不是模型差，是你抽帧没抽到关键帧，或者transcript漏了关键句。

---

## 最大问题十：真实push体验里的“频控、重复、疲劳”没有进入case

你现在的评估单元是单条：

```text
(video, persona, trigger) → title/body
```

这适合v0.1。但push体验常常不是单条造成的，而是序列造成的：

```text
同一个creator连续推
同一个topic连续推
用户多次不点还继续推
多个渠道push/EDM/SMS重复触达
短时间内相似hook重复出现
```

这和你业务里已经讨论过的点高度相关：push的分布、极值、离群点、同一作者对同一用户的推送密度，都可能比平均质量更关键。

建议v0.2加一个“sequence eval”小集，不用很大：

```text
20个用户序列
每个用户最近10条push history
当前候选video + copy
评估是否重复、疲劳、过度打扰、是否该降级
```

新增维度：

```text
Fatigue Risk
Redundancy with Recent Pushes
Creator/Topic Overexposure
Negative History Respect
```

这会让eval从“文案生成质量”升级到“push体验质量”。

---

## 我建议你把文档改成三层eval体系

现在文档是一个大一统benchmark。工业界更推荐拆三层。

### Layer 1：Generation Quality Eval

回答：

```text
模型是否能基于视频生成准确、自然、可点击但不误导的文案？
```

保留你现在大部分rubric：

```text
可读性
自然度
表达力
视频相关性
内容忠实度
预期一致性
长度合规
```

### Layer 2：Push Suitability Eval

回答：

```text
这条文案是否适合主动发给这个用户？
```

新增/强化：

```text
Notification Worthiness
User Benefit Strength
Lifecycle Tone Fit
Fatigue Risk
Annoyance Risk
Do-not-send Correctness
```

### Layer 3：Deployment Selection Eval

回答：

```text
哪个系统配置值得进入线上A/B？
```

指标是：

```text
confirmed safety rate
core quality score
push suitability score
cost per generated candidate
latency p50/p95/p99
fallback rate
judge uncertainty rate
```

然后再做Pareto。

这样比一个总分更清楚：
**第一层看会不会写，第二层看该不该发，第三层看值不值得上。**

---

## 你可以优先改的P0清单

我会按优先级这么改：

| 优先级 | 修改项                                                     | 为什么重要                 |
| --- | ------------------------------------------------------- | --------------------- |
| P0  | 把统计主口径改成video-cluster bootstrap / hierarchical analysis | 现在N=390可能高估显著性        |
| P0  | 新增Notification Worthiness维度                             | Push场景核心，不补会偏feed标题评估 |
| P0  | 安全OR机制增加confirmed violation口径                           | 避免judge误报放大导致误杀       |
| P0  | human review改成per-dimension、多rater、冲突仲裁                 | 否则judge校准不够可信         |
| P0  | 增加强baseline：规则模板、cheap metadata LLM                     | 避免只证明LLM打赢弱baseline   |
| P1  | 用户相关性拆成Preference Fit和Lifecycle Tone Fit                | 避免把策略预设写进质量分          |
| P1  | 增加do_not_send / fallback输出                              | 更接近真实生产系统             |
| P1  | 加入sequence/fatigue小集                                    | 解决push体验里的重复与打扰问题     |
| P1  | 把输入变量改称system configuration，并记录preprocess质量             | 避免错误解释B/C/D结果         |
| P2  | 扩视频样本到80-150，减少persona全乘法                               | 提升泛化与统计可信度            |

---

## 最后给一个“更像工业界决策”的结论模板

你现在文档最后会产出Pareto front。我建议结果分析时不要只说“谁在front上”，而是输出这种形式：

```text
推荐进入线上A/B的候选：
1. Primary candidate：模型X + varC
   - 理由：质量接近最高，成本显著低于full video，安全通过
   - 风险：在美妆/宠物视觉主导内容上弱于varD

2. Premium candidate：模型Y + varD
   - 理由：视频理解最好，预期一致性最高
   - 风险：成本/延迟高，只适合高价值用户或高价值视频

3. Budget candidate：模型Z + varA/C
   - 理由：成本极低，质量显著高于production baseline
   - 风险：低信号视频上容易泛化，需更强fallback
```

再加一句：

```text
不建议上线的候选：
- 安全确认违规率高
- judge uncertainty高
- 相比baseline无显著提升
- 成本高但质量不显著优于低成本方案
```

这样它就从“研究报告”变成“能支撑PM/算法/工程决策的选型报告”。

---

## 我的整体判断

这份文档最值得保留的是：**问题拆得清楚、变量设计有工程意义、rubric有层次、统计方法不幼稚、局限写得诚实。**

最需要修的是：**不要让390个case制造虚假的统计信心；不要让LLM judge的误报决定安全生死；不要只评单条文案，而要补push特有的打扰价值和疲劳风险；不要只跟弱baseline比。**

把这些补上，它就不只是一个“LLM文案eval”，而是一套真正能进入工业界评审的**push copy generation offline evaluation framework**。
