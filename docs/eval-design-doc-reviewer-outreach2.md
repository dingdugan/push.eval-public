我完整看完了。总评：**这已经不是“随便写个eval方案”，而是一个接近工业级offline eval design doc的版本**。结构、scope、实验矩阵、指标、聚合、统计方法都很完整，尤其是你把问题拆成「输入信号边际」和「模型能力分布」，并且明确offline eval和online A/B的分工，这个方向非常对。文档里也已经把评估单元定义为`video × persona × trigger`，再映射到`model → title/body`，整体框架是清楚的。

但如果我从eval专家视角挑刺，我会说：**它现在的问题不是“不专业”，而是太像一个最终研究论文/实验计划，稍微欠缺“生产决策型eval”的锋利度。** 也就是说，它能产出很多漂亮表格，但还需要更清楚地回答：**上线决策到底怎么被这个eval改变？什么结果会让我们选A，什么结果会让我们否掉整个方向？**

---

## 1. 最大优点：问题拆得很对

你把eval目的定义成「输入×LLM」在效果、成本、耗时、安全上的mapping，用来支撑push文案生成方案选型，这个是很好的。更重要的是，你不是泛泛地测“哪个模型好”，而是拆成两个有决策价值的问题：**输入信号边际**和**模型能力分布**。这比很多团队只做“模型榜单式eval”强很多。

尤其是A/B/C/D四种输入设计很有洞察：

* A：metadata-only，代表低成本baseline输入。
* B：A+keyframes，测视觉静态信息。
* C：A+audio transcript，测便宜但高信息量的语音文本。
* D：full audio-video，测理论上限。

你还明确提出“C是否支配B”这个问题，这就很好，因为它不是为了炫多模态，而是在问一个真实工程问题：**要不要为图像/视频token付钱？**

---

## 2. 第二个优点：安全门和Pareto设计是对的

你没有把安全、效果、成本、耗时揉成一个大总分，而是把安全作为hard gate，再在效果、成本、耗时之间做Pareto。这是很成熟的eval思路。因为安全和效果确实不可通约：一个文案再会拉CTR，只要误导、操纵、泄露隐私，就不应该靠高效果分“平均回来”。

这个设计比“安全20%、质量50%、成本20%、速度10%”那种拍脑袋加权要好。后者的问题是，最后谁赢完全取决于权重；你的Pareto设计至少能保留多目标决策的诚实性。

---

## 3. 第三个优点：baseline设计很务实

你没有用“人工专家改写”作为baseline，而是用真实生产里可能存在的status quo：`{creator_name} just posted:` + `{video_title}`。这个选择是对的。因为你的核心问题不是“LLM能不能超过人类编辑”，而是“LLM是否值得替代当前系统生成方式”。文档里也明确说所有LLM output都要和同case baseline做paired对比，回答“LLM方向是否系统性优于status quo”。

这点非常关键。很多eval会犯一个错误：找一个很强但不可生产化的人类golden answer做baseline，最后证明模型不如人类，然后结论对业务没有帮助。你的baseline是生产可比的。

---

# 我主要建议你改的地方

## 1. 你需要加一个“上线决策规则”章节

现在文档讲了怎么测，但没有足够明确地讲**测完以后怎么拍板**。比如最终可能出现这些结果：

* Gemini+D效果最高，但成本高5倍。
* Kimi+C效果只低0.15分，但成本低70%。
* GPT+A比baseline显著好，但安全率低于阈值。
* C显著好于B，但只在学习/时事类有效。
* 所有模型都显著高于baseline，但用户相关性维度不显著。

这些情况下，谁赢？目前文档会产出Pareto front，但PM/业务方仍然需要二次解释。

我建议加一个章节，叫：

**5. Decision Policy / Launch Recommendation Rule**

里面写清楚三层规则：

**第一层：方向判断**

* 如果没有任何`model × input`组合在核心效果分上显著高于baseline，并通过安全门，则不建议推进LLM push copy方向。
* 如果至少一个组合显著高于baseline，并通过安全门，则进入方案选择。

**第二层：候选筛选**

* 必须通过安全门。
* 必须在baseline上有统计显著提升。
* 成本不能超过status quo某个可接受上限，或者相对最佳便宜方案的效果提升必须超过某个最小差值。

**第三层：推荐方案**

* 默认选择Pareto front里成本最低、效果接近最优的方案，而不是效果绝对最高的方案。
* 如果最高效果方案相对便宜方案的提升小于MDE，比如0.2或0.3分，则选择便宜方案。
* 如果某方案只在特定垂类胜出，则推荐“分垂类routing”，而不是全局替换。

这会让你的doc从“研究设计”变成“决策机器”。

---

## 2. 你的persona设计很聪明，但“用户相关性”可能会被judge过度主观化

你现在的persona设计是：13个合成persona，覆盖lifecycle和内容偏好。这个框架本身不错。你还意识到不同lifecycle下“个性化”的定义不同：cold-start不能硬凹，engaged要精准命中，at-risk/dormant要敢换hook。这个是很好的产品直觉。

但风险在于：**用户相关性这个维度很可能变成LLM-judge最不稳定、最容易“脑补产品策略”的维度。**

比如at-risk/dormant里，你定义“好=敢换hook”。但真实业务里不一定总是这样。一个dormant用户如果历史上只看宠物，给他推一个陌生时事视频，可能不是“敢换hook”，而是“彻底错配”。这里要区分两件事：

* **内容匹配**：这条视频和用户兴趣是否匹配？
* **召回策略/挽回策略**：对降活用户是否应该探索新方向？

你现在把这两件事揉进了“用户相关性”。这会让judge很难稳定打分。

我建议把“用户相关性”拆成两个更干净的指标：

1. **Interest fit**：文案是否准确利用了persona里的兴趣信号。
2. **Lifecycle-appropriate framing**：语气和hook是否适合该生命周期。

这样更稳。比如：

* engaged用户：interest fit权重更高。
* cold-start用户：framing权重更高。
* at-risk/dormant用户：允许exploration，但不能把“不匹配”直接解释成“大胆换hook”。

否则LLM-judge会倾向奖励“看起来很懂产品策略”的文案，而不一定奖励真实用户想点的文案。

---

## 3. 你需要补一个“Prompt Design & Output Contract”章节

现在文档对eval数据、评分、统计写得很细，但对**被测模型到底收到什么prompt**描述略少。你写了所有模型用同一prompt模板、system prompt一致、输出title/body，但还不够。

在LLM eval里，prompt本身是模型表现的一部分。尤其是push copy生成任务，prompt会强烈影响：

* 是否使用夸张hook。
* 是否隐含操纵性语言。
* 是否严格遵守长度。
* 是否输出多个候选还是一个候选。
* 是否解释理由。
* 是否引用用户persona。
* 是否过度个性化。

建议你补一页模板，至少包括：

```text
System:
You are a push notification copywriter for a short-video app.
Your goal is to write concise, truthful, non-manipulative push copy.
Never imply private messages, emergencies, time pressure, or facts not shown in the video.

User:
Trigger: The system is recommending a video from an unfamiliar creator.
Persona: ...
Video input: ...
Output exactly JSON:
{
  "title": "...",
  "body": "..."
}
Constraints:
- title <= 50 chars
- body <= 150 chars
- no clickbait
- no FOMO
- no claim not grounded in video
```

并且建议加一个**negative instruction library**：

* 不要写“you won’t believe…”
* 不要写“before it’s gone”
* 不要写“someone sent you…”
* 不要暗示好友/私信/紧急事件。
* 不要编造视频没有出现的事实。

这会让你的eval更可复现，也更像真实production prompt tuning。

---

## 4. LLM-judge设计很完整，但有一个核心偏差：judge用完整视频，generator不一定用完整视频

你规定LLM-judge始终以完整视频信息作为参考，无论被评copy是A/B/C/D哪个输入生成的。这个原则总体是对的，因为judge应该知道ground truth。

但这里有个微妙问题：**当generator只拿metadata-only时，它不知道的视频事实，judge却知道。** 这会导致A方案在“视频相关性/内容忠实度/预期一致性”上天然吃亏，而这当然也是你想测的输入信号边际。但要注意：有些低分不是模型能力差，而是输入不可观测。

所以建议你在结果里显式区分两类错误：

1. **Input-limited error**：输入里没有信息，模型无法知道。
2. **Model-induced error**：输入里有信息，但模型误读或乱编。

这个区分非常重要。否则你可能只得到“full video最好”的废话。真正有价值的是：

* metadata-only缺哪些信息？
* transcript能补哪些信息？
* keyframes能补哪些信息？
* full video比transcript多出来的收益到底来自哪里？

可以在judge prompt里让judge多打一个字段：

```json
"error_attribution": "input_missing | model_misread | generation_style | no_error"
```

这样结果分析会锋利很多。

---

## 5. 安全聚合用OR很保守，但可能过度惩罚高variance模型

你现在安全侧是两层OR：两个judge任一判违规就违规；同cell三次重复任一unsafe，整个cell unsafe。这个是“宁严勿纵”的设计，适合作为安全粗筛。

但它也会引入一个问题：**模型只要采样方差大，就会被严重惩罚。** 例如一个模型3次里有1次轻微FOMO，整个cell变unsafe；另一个模型稳定输出平庸文案，就更容易过安全门。

这在生产安全上可以接受，但建议你同时报告两个安全口径：

* **Strict cell safety rate**：你现在的OR口径，用于上线门槛。
* **Output-level violation rate**：所有outputs里违规占比，用于诊断模型稳定性。
* **Violation severity distribution**：误导、操纵、有害、隐私分别报，不只报总安全率。

这样你能区分：

* 模型A：偶发轻微营销腔。
* 模型B：少量但严重编造事实。
* 模型C：高频擦边但不严重。

这三种风险的产品决策完全不同。

---

## 6. 统计方法很认真，但复杂度可能超过当前样本能支撑的解释力

你用了Wilcoxon、Bonferroni、bootstrap CI、Mann-Whitney、power估算，这些都合理。

但我会提醒一点：你的实验规模看起来很大，16,380次调用；但真正的独立内容样本只有**30条视频**。390个case来自30 video × 13 persona，persona是合成扩展出来的，不等于390个完全独立样本。

这意味着统计上有一个cluster问题：同一个video派生出来的13个persona case并不独立。你现在按390 case做bootstrap和paired test，可能会**低估不确定性**。

更稳的做法是：

* 主CI用**cluster bootstrap by video_id**，不是普通case bootstrap。
* 显著性分析里至少做一个robustness check：按video聚合后再比较。
* 汇报时说清楚：case-level结果用于精细诊断，video-cluster-level结果用于主结论稳健性。

这点很重要。因为真正上线时，你关心的是“换一批视频是否还成立”，而不是“同一批30条视频换13个persona是否成立”。

---

## 7. 样本设计还有一个隐性缺口：缺“坏视频/难视频/边界视频”

你现在的视频采样按垂类、发布时间、view量分层，这很好。但push copy eval里，真正容易翻车的不是普通视频，而是边界内容：

* 标题党视频。
* 反讽/讽刺视频。
* high-context meme。
* 有争议的新闻/政治/健康建议。
* 视频里有敏感信息但不该写进push。
* creator标题本身误导。
* 视频亮点在最后才出现。
* 评论区和视频内容相反，比如评论在嘲讽作者。

你现在的高view/中view分层能部分覆盖difficulty，但不一定覆盖这些**risk slices**。

我建议你在30条里强行加入一个小的“challenge set”，比如6条：

* 2条容易误导/标题党。
* 1条隐私风险。
* 1条反讽/meme。
* 1条健康/金融/安全建议类边界内容。
* 1条视频亮点很晚才兑现。

然后主集仍然保持分层，challenge set单独报告，不混入主效果均值。这样你的安全和忠实度eval会更有牙齿。

---

## 8. 成本口径建议加入“production cost model”，不只是API单次成本

你已经记录token、视频计价、latency，很好。但对于consumer-scale push，真正的成本不只是单次生成成本，还包括：

* 是否能batch生成。
* 是否能缓存同一video的content understanding结果。
* persona是否只影响最后一段copy，还是每个persona都要重新看视频。
* 是否可以两阶段：video summary一次生成，多persona文案用summary生成。
* 是否需要pre-generation，还是real-time generation。

你现在的pipeline是`video, persona, trigger → model → title/body`，这在eval上干净，但在生产上可能成本偏高。更工业的production方案可能是：

**Stage 1：video understanding**
`video → canonical video summary / hooks / safety facts`

**Stage 2：persona-conditioned copy**
`summary + persona + trigger → title/body`

这会极大改变成本和latency，也会让A/B/C/D的结论更可用。建议你至少在doc里加一个“production architecture implication”：

* 本eval测的是end-to-end生成。
* 如果C或D显著更好，下一阶段要评估“先生成视频摘要，再多persona复用”的架构。
* 成本汇报里加入“per video fixed cost”和“per user marginal cost”。

否则你可能会低估full video方案的生产可行性：它贵在每次都看视频，但如果每条视频只理解一次，成本结构就变了。

---

# 我会怎么给这个doc打分

如果按eval design doc标准，我会给：

**8.3/10。**

它已经有很强的结构感和专业度，尤其是：

* 研究问题明确。
* 输入消融设计好。
* safety gate正确。
* baseline务实。
* persona条件化有产品理解。
* 统计方法认真。
* 已知局限写得诚实。

扣分主要在：

* 缺少上线决策规则。
* 用户相关性指标有点混合了兴趣匹配和生命周期策略。
* 没有充分处理video-level cluster dependency。
* 缺少challenge set。
* 成本模型偏eval调用成本，不够production architecture cost。
* prompt/output contract还不够显式。

---

# 我建议你下一版优先改这5处

按优先级：

1. **加Decision Policy章节**：明确什么结果上线、什么结果不做、什么结果进入A/B。
2. **把用户相关性拆成Interest Fit和Lifecycle Framing**：减少judge主观性。
3. **加video-cluster bootstrap / video-level robustness check**：避免390 case伪独立。
4. **加challenge set**：专门测误导、隐私、反讽、标题党、晚兑现。
5. **加Prompt & Output Contract**：把generator prompt、judge prompt、JSON schema都固化。

一句话总结：**这个doc已经像一个认真研究员写出来的eval方案；下一版要把它推成一个能服务产品上线决策的eval系统。**
