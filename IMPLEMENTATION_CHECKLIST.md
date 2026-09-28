# push.eval — Implementation Checklist

> 按 [user CLAUDE.md](~/.claude/CLAUDE.md) Plan→Code→Ship 工作流维护.
> 每条做完打 `- [x]`, 跟代码改动同一 commit 落库. 共识推翻的条目划 `- [~]` + 注明原因.

---

## W2: 数据 + var A/B generator (Day 8-14)

### Day 8 准备 (本会话, 启动 W2 第一笔代码)

- [x] 建 repo 骨架: `src/` `data/preprocess/{keyframes,transcripts}` `results/` `notes/` + `.env.example` (列 6 个 key 占位) + `requirements.txt` + 更新 `.gitignore`
  - 证据: `.env.example:1`, `requirements.txt:1`, `.gitignore:29-39` (新加预处理二进制忽略段), `src/__init__.py:1`, `data/personas.jsonl:1`, `notes/worklog.md:1`; 目录 `data/preprocess/keyframes/` `data/preprocess/transcripts/` `results/` 由 mkdir -p 建好 (空目录, git 不追)
- [x] 装本地工具链: brew install yt-dlp ffmpeg + .venv + pip install (Whisper / PySceneDetect / google-api-python-client / openai / google-generativeai / pydantic + eval_type_backport) — 验证 13 schema models 通过 cold-start↔UNKNOWN validator + 32 抽样格枚举正确
  - 证据: `which yt-dlp` → `/opt/homebrew/bin/yt-dlp` (v2026.03.17), `which ffmpeg` → `/opt/homebrew/bin/ffmpeg` (v8.1.1); `.venv/bin/pip list` 含 pydantic 2.13.4 / openai-whisper 20250625 / scenedetect 0.6.7.1 / openai 2.38.0 / google-api-python-client 2.196.0 / google-generativeai 0.8.6 / yt-dlp 2025.10.14 / pandas 2.3.3 / jsonlines 4.0 / python-dotenv 1.2.1
- [x] 实现 `src/schemas.py`: VideoRecord / PersonaRecord / TestCase / GenerationOutput / LLMJudgeScore / HumanReviewScore / OutputRow / BaselineRow, 含 cold-start ↔ UNKNOWN 自洽 validator (兑现 design-doc § 2.4 + § 4.3 字段)
  - 证据: `src/schemas.py:1` (320 行, 13 个 pydantic models); `PersonaRecord._check_consistency` validator 在 `src/schemas.py:124`; smoke test `PYTHONPATH=src .venv/bin/python -c "from schemas import PersonaRecord, ..."` import 无错
- [x] 生成 13 persona: 按 design-doc § 2.2 表硬码 → `data/personas.jsonl` (13 行) + 自洽性校验脚本 `src/build_personas.py` (8 垂类全覆盖 / 每垂类 3-5 次 / lifecycle 5 档全覆盖)
  - 证据: `data/personas.jsonl` (13 行, `wc -l` 确认); `src/build_personas.py:130` `_verify_coverage()` 自洽校验函数; 运行输出 `[OK] 落盘 13 persona → data/personas.jsonl` + lifecycle 分布 cold-start:1 / exploring:3 / engaged:3 / at-risk:3 / dormant:3
- [x] 写 `src/curate_videos.py` 框架: YouTube Data API client + 8 垂类 × 2 时段 × 2 view 档 query + filter (英语 / 1-5 min) + 落 `data/videos.jsonl`; 不真跑等 YouTube key
  - 证据: `src/curate_videos.py:1` (372 行, `stage_a_search_candidates` 在 `src/curate_videos.py:226` + `stage_b_fetch_metadata` 在 `src/curate_videos.py:271`); smoke test `PYTHONPATH=src .venv/bin/python -c "from curate_videos import all_cells; cells = all_cells(); print(len(cells))"` 返回 32; 32 抽样格 = 8 垂类 × 2 时段 × 2 view 档 对齐 design-doc § 2.1
- [x] 落 `notes/worklog.md` (Day 8 准备日 entry + Python 3.9 caveat + 偏差记录)
  - 证据: `notes/worklog.md:1` (含 Day 8 准备日 entry + 已完成清单 + 3 个 caveats + Day 8/11 真执行依赖列表)

### Day 8 真执行 (抽样设计第一性重构后, 见 notes 2026-05-30)

旧 32 抽样格方案已推翻 (实测 10 格空 + view 档后验筛会死循环 + 老/新跟 view 混淆), 详 `notes/worklog.md` 2026-05-30. 新方案: 8 官方分类 × 每类 4 = 32 视频, 最近窗口, medium≤8min, view 退成连续协变量.

- [~] ~~旧: 8 垂类 × 2 时段 × 2 view 档 32 格 curate 30 视频~~ — 推翻, 原因见 notes 2026-05-30 (10 格空 / view 后验死循环 / 老新 view 混淆)
- [x] 实测 YouTube 接口取数边界 (探针 `src/probe_youtube_api.py` + `probe_youtube_api2.py`): 翻页救不了中等档 / Shorts 占 74-86% / medium 档物理排除 Shorts / 无 view 过滤 filter
  - 证据: `notes/worklog.md` 2026-05-30 触发问题段; 两个探针脚本在 `src/`
- [x] 成本模型 `src/cost_model.py`: 32 视频 W2 ~$76 / 全项目 judge 缓存+batch ~$840 / 人工锁 ~400 条 ~20h
  - 证据: `src/cost_model.py`; user 据此锁定 N=32 + judge 缓存+batch + 人工固定 400 条
- [x] **确认 8 个官方 category id** (user 选 A): Music(10) / Pets&Animals(15) / Gaming(20) / News&Politics(25) / Education(27) / Howto&Style(26) / Sports(17) / Science&Technology(28)
  - 证据: `src/schemas.py` `CATEGORY_ID` dict (CATEGORY_MAP_PASS 断言通过); notes 2026-05-30 "新抽样设计" 段
- [x] 实测定稿 YouTube 取数法 (今天连撞 3 个 API 坑, 详 notes 2026-05-30):
  - 坑1: `videoCategoryId` 单用返回 0, 必须配 `q` 关键词 (它是过滤器不是查询)
  - 坑2: `q` 的 `|` OR 是**词级**, 拼不了多词短语 ("a b|c d" 命中近 0)
  - 坑3: `videoCategoryId` + `videoDuration` 同用返回 0
  - 定稿: 每类 **5 个真人 query 各跑一次 + category 硬过滤 → union**; 时长**后验**过滤
  - 证据: `src/category_queries.py` (8类×5 query); `/tmp/or_focus.json` 实测; commit c1a522d
- [x] `src/curate_videos.py` 实现1 + 参数化 + yt-dlp 兜底:
  - `search_one_category` 5 query 分跑合并; `--min-sec/--max-sec/--window-days` 可调 (默认 181/480/90)
  - `finalize_ytdlp` + `--source ytdlp`: 零配额抓 metadata+评论 (配额耗尽兜底, 实测通)
  - 证据: CURATE_PARAMETERIZED_OK + YTDLP_FALLBACK_OK 断言; commit c1a522d + 5c5c5c5

**▶▶ Day 8 真执行 (2026-05-31 配额回血后跑完) ▶▶**

- [x] **跑 stage A**: `curate_videos.py a --min-sec 90` (下限放 90 捞全分布); 候选数 Music/Pets/News/Edu/Howto 各30 / SciTech24 / Gaming21 / Sports14; 配额 ~4.1k/10k
  - 证据: `data/video_candidates.jsonl` (8类候选, `wc -l`=209 = 各类 30+30+21+30+30+30+14+24); `data/curate_done_cats.txt` 8 类全标记完成; `/tmp/curate_stageA.log` 末尾 "[OK] stage A 完成"; cap 改动 `src/curate_videos.py:53` (CANDIDATES_PER_CATEGORY=30)
- [x] **实测时长分布定下限**: 各类 ≥181s 累计 Music21/Pets5/Gaming9/News16/Edu18/Howto10/Sports6/SciTech8 → 8 类全 ≥5 → **user 拍 >3min (181s)**; 推翻了"181 供给不足"担忧
  - 证据: 分桶脚本实测输出 (本会话, 读 `data/video_candidates.jsonl` 按 duration_sec 分桶); notes 2026-05-31 entry "时长分布" 段; AskUserQuestion 答 ">3min (181s)"
- [~] ~~不足的类补跑 --min-sec 120~~ — 不需要, 181 下每类都 ≥4
- [x] **跑 stage B**: 生成 `data/video_picks.tsv` (每类滤≥181 再 view top4=32) → `curate_videos.py b` (API源) → `data/videos.jsonl` 32 条
  - 证据: `data/video_picks.tsv` (32 行 + 注释头); `data/videos.jsonl` (`wc -l`=32); stage B stdout "[OK] 32 视频 → data/videos.jsonl"
- [x] 人工抽查: 复验 32/32 schema合法 + 时长190-456s全∈[181,480] + 8类各4 + 抽查 V01/V13/V25 metadata完整; V02 评论被关→空(优雅降级); V20 擦边标题 user 选保留
  - 证据: 复验脚本输出 (本会话, `VideoRecord(**r)` 重验 32/32 + Counter 8类各4 + duration min/max=190/456 + comments mean=9.7 / V02=0); 抽查 V01/V13/V25 title+desc+tags+comment 实测打印
- [x] `notes/worklog.md` 记录: quota ~4.1k / 窗口 90天 / 下限 181 / view 0.07M-130M / 各产物 (2026-05-31 entry)
  - 证据: `notes/worklog.md` "## 2026-05-31 (W2 Day 8 真执行..." 整段

### Day 9 数据预处理

- [x] 预处理 pilot (5 条 V01/V13/V17/V21/V25 覆盖分类) → PySceneDetect + ffmpeg 抽帧 + Whisper-large-v3 转写, 人工 verify 转写质量
  - 证据: pilot 验出 4 个坑 (MPS 崩→CPU / 音乐慢→condition_on_previous_text=False+temp0 / IP 封→cookie / 低语音语种误判), 全解决固化进 `src/preprocess.py`; 详 notes 2026-05-31 Day 9 段
- [x] 批量 32 视频 keyframes: yt-dlp 下载 → PySceneDetect → 每 scene 中间帧 (cap 5) → `data/preprocess/keyframes/{Vxx}/frame_{i}.jpg`
  - 证据: 32/32 都有帧 (`glob keyframes/*/frame_*.jpg` 核对); V02=1帧/V17=3帧因镜头<5, 符合 cap 逻辑
- [x] 批量 32 视频转写: Whisper-large-v3 → `data/preprocess/transcripts/{Vxx}.json` (segment 时间戳 + avg_logprob)
  - 证据: 32/32 transcript 存在; V29-32 用 cookie 绕反爬补回; V23 用 temp0 补回 (原 timeout)
- [x] 填充 PreprocessMeta (+新增 transcript_language) → `data/preprocess/preprocess_meta.jsonl` 32 行 0 error
  - 证据: 全量表见 notes; 弱音轨 V11/V22/V23/V24 (var C 信号弱) + 真非英语 V01(pa)/V21(hi) 已诊断标注

### Day 10 schema 改造 + persona 重建 + test case + baseline

- [~] ~~13 persona 已在 Day 8 准备阶段产出~~ — 偏好垂类需重映射到新 8 官方分类, 重建 (见下)
- [x] 改 `src/schemas.py`: 去 `publish_period` / `view_tier` enum; `Vertical` → `Category` (官方 id+名); `VideoRecord` 用 `category`/`category_id`/`view_count`(连续)/`duration_sec`≤600; `OutputRow` 去 `publish_period`
  - 证据: `src/schemas.py` `Category` enum + `CATEGORY_ID`; `grep Vertical/PublishPeriod/ViewTier` 无残留; ALL_IMPORTS_AND_SCHEMA_PASS
- [x] 重建 `data/personas.jsonl`: 13 persona 偏好重映射到 8 官方分类 (食物/美妆→Howto&Style, 健身→Sports, 新增 Science&Tech), 每类 3-4 次 (总 28 槽) + cold-start↔UNKNOWN 自洽
  - 证据: `src/build_personas.py` 重写 + `_verify_coverage()` 通过; 直接读 `data/personas.jsonl` 验: 13 行, 8 类全覆盖 (Music4/Pets3/Gaming4/News3/Education4/Sports4/Howto3/SciTech3=28)
- [x] 笛卡尔积 32 video × 13 persona = 416 test case → `data/test_cases.jsonl` (`src/build_test_cases.py`)
  - 证据: `data/test_cases.jsonl` 416 行; 脚本自检 case_id 唯一 + baseline 全非空通过 ("[verify] 416 行..." 输出)
- [x] 派生 baseline B0 (现状) + B1 (规则启发式) → 每视频一份写进各 case
  - 证据: B0=`{channel} just posted:`/`{title}` (§1.2 反例); B1=`New video | {category} · {channel}`/`{title} — from a {category} creator you might like`; 抽查 V01_P1 / V08_P1 实测打印正确
  - 决策: doc § 2.4 的 B1 模板是中文示意, 本项目英文内容 → B1 译成英文 (跟 LLM output 同语言才能 paired 对比, 语义一一对应)
- [x] 人工抽查 test case (覆盖不同视频/persona), 验证 schema 一致
  - 证据: 抽查 V01_P1 (Music/cold-start) + V08_P1 (Pets/cold-start) baseline 字段完整正确; 全 416 经 TestCase pydantic 构造无报错

### Day 11-12 var A generator

- [x] 写 `src/run_generation.py`: var A 渲染 prompt + 4 vendor 调用分支 + pilot(观测)/run(全量: 并发+落盘 OutputRow+断点续跑) 两模式
  - 证据: `src/run_generation.py`; run 模式小批 15 条验证落盘 schema 15/15 + resume 全跳过 (notes 2026-06-01)
- [x] 模型调用 pilot: 4 模型实测 cost + 坑 (输入~480token / Kimi 关thinking / Gemini billing+503 / MAX_TOKENS 2000→8000)
  - 证据: notes 2026-06-01 pilot 段; 全量 var A 修复后 ≈ $13 (3模型) / $17-22 (+gemini)
- [x] **全量 var A**: gpt/deepseek/kimi 跑 var_A_raw.jsonl + gemini-2.5-flash 并行跑 var_A_gemini.jsonl 后合并 → **4992 行 OutputRow**, schema 100%, 总成本 **$22.64**
  - 证据: `results/var_A_raw.jsonl` 4992 行 (各模型 1248); 各模型长度合规 gpt100%/kimi88%/deepseek87%/gemini83%, parse 全 100%; 详 worklog 2026-06-01
- [x] **Gemini 降级 3.5-flash→2.5-flash** (user 拍): 3.5-flash 持续 503 (新模型容量约束, 已知 1-3 周); 2.5-flash 顶替 ($0.30/$2.50). call_gemini 参数化 + PRICES 加条目
- [x] verify: 4992 = 416×3×4; schema 4992/4992 合法; pandas group-by 可切 (model/case/video/persona/run); judge/human 字段留空待 W3
- 注: 控制变量 T=0.7 已作废 (reasoning 模型锁/忽略, 见 design-doc § 4.2 改写); 各模型用默认采样, Kimi 关 thinking

### Day 13-14 var B generator

- [x] var B input 构造器: `render_user_prompt(case,"B")` + `load_keyframes` 5帧 + 各 vendor 图像封装 (Gemini Part.from_bytes / OpenAI兼容 image_url base64)
  - 证据: `src/run_generation.py`; 三重隔离 (独立文件/var_label=B/cell_id|B|) 代码确认
- [x] var B pilot: 9 条验证 var_label=B + cell_id|B| + n_keyframes=5 + **tokens_in 暴涨证明图真传** (gemini 534→1893 / gpt 512→6128 / kimi 524→6630)
- [~] ~~4 模型~~ 全量 var B: **3 模型** (gemini-2.5/gpt-5.5/kimi, DeepSeek 纯文本跳过) × 416 × 3 = 3744 → `results/var_B_raw.jsonl`
  - 证据: 3744 行 schema 100%, 总成本 $56.50; 2 条 gemini 瞬时失败 resume 补回
- [x] verify: tokens_in 显著高于 var A (图像 token 已验); 早期信号 加关键帧后长度合规降 (gemini 83→73% / kimi 88→83% / gpt 100→100%)
- [x] `notes/worklog.md` 收尾: var A $22.64 / var B $56.50; design-doc 偏差 (T=0.7作废/Gemini降级/DeepSeek纯文本) 均已记 + 改

### W2 收尾 ship 检查

- [x] 跑 cold-start walkthrough (规则 3): var A/C 可从 fresh clone 复现 (test_cases.jsonl 自足 + transcripts 在库); var B 需先重生成 keyframes; **断点 P0 = README 过时 (W1 字样) → 已重写** (现状表+复现 pipeline+在库vs重生成+模型清单+Gemini降级)
  - 证据: README.md 重写; test_cases 自足验证 (顶层 video+persona+baseline); .env.example 列全 6 key
- [x] `notes/worklog.md` 整理 W3 衔接: 5 处 design-doc 假设被打脸 (抽样/T=0.7/Gemini降级/DeepSeek纯文本/cost) + W3 待做 (var C/D + LLM-judge + 人工抽检) 已列, 见 worklog 2026-06-02 W2 收尾段

---

## W3 Day 1: judge reality-probe (先验机制再跑全量, retro 头号教训)

- [x] judge reality-probe `src/judge_probe.py`: 3 真 var_A output (cold-start/engaged/at-risk, 挑非 judge 模型避免自评) × gemini-2.5-flash + kimi-k2.6, 传完整视频
  - 证据: 视频可传 (gemini Files API / kimi base64 内联 video_url); 合法 JSON safety4+effect9 gemini3/3+kimi3/3; cold-start preference_match=null 两家正确; 修 gemini max_output_tokens 4000→8000(截断)+503退避; 无缓存 gemini $0.0235/kimi $0.047 每条; 详 worklog 2026-06-02 W3 Day1
- [x] judge 视频缓存验证 `src/judge_cache_probe.py` + `src/judge_cache_equiv.py`: Gemini context cache 可用 (cached=58159 token, 省~57%/正好印证 cost_model JUDGE_CACHE_FACTOR=4) + **分数等价性受控验证 漂移维度数=0** (缓存对打分透明)
  - 证据: §912 关缓存只管生成调用 (其成本是报告产出), judge 是 scope 外基础设施; doc 更新时落 scope 边界说明
- [x] 纠正 handoff ungrounded "$470": doc 无此数; 旧估实为 cost_model $840(缓存+batch); 无缓存实测外推 ~$1155 (32760 call 带视频)

## W3 Day N: var C / var D 生成 + 全量 judge (依赖序)

- [x] var C 生成: `render_user_prompt(case,"C")` = metadata + 音轨转写文本; 4 模型 × 416 × 3 = **4992 行齐** (每模型 1248 ✅) → `results/var_C_raw.jsonl`; 总成本 ~$25。执行侧边际 (A→C, 非质量分): tin 翻倍(+~800 音轨 token); 长度合规率普遍降 (kimi −16.7pt / gemini −5.8 / deepseek −4.6 / gpt 0)——上下文变长→更易超字数; 质量分边际待 judge。
  - 证据: `wc -l results/var_C_raw.jsonl` = 4992; 每模型分布 1248×4 (本会话核对脚本); 边际表 + 诚实边界 (非质量分) 见 `notes/worklog.md` 「2026-06-03 var C 续跑完成」段
- [x] var D 生成: 原生视频 gemini-2.5-flash + kimi-k2.6 × 416 × 3 = **2496 行齐** → `results/var_D_raw.jsonl`; 成本 $92 (pilot 估 $90)。gemini V07 被 vendor block (39 条空, block_reason=OTHER, 保留作结果); kimi V07 39/39 覆盖 ✅。
  - 证据: 质检 qa_data.py 全过 (gemini 1248 + kimi 1248, 0 重复, run_id 各416, video_attached 100%); kimi $54.96 vs pilot 估 $54.5; 三天弱网磕完记录见 worklog 2026-06-09~11 (--sort-size + watchdog v3 + timeout 组合)。
- [x] **judge 池定案: 抽样双评 + Doubao-lite 主评** (user 拍板; doc §4.3 Step1 已改): 主评 Doubao-2.0-lite 评全量 + 副评 Gemini(+Kimi 评 gemini output) 仅 κ 抽样; 全量 judge ~$215-245 (vs W1 ~$1,155)
  - 证据: 视频 judge 能力矩阵全实测 (Gemini/Doubao/Kimi ✅; GPT/GLM/DeepSeek/Qwen 出局); Doubao cold-start 5/5; worklog 2026-06-03
- [x] judge runner **代码就绪 + smoke 验通** (`src/judge_runner.py`): 仿 run_generation.run_full (并发+落盘+续跑+parse-fail重试); 主评 Doubao-lite + 副评配对(避自评); **键名映射** judge JSON↔schema (4 处不同名) + Pydantic 校验。全量待 var D 齐 (L134)。
  - 证据: 主评 Doubao smoke 3/3 OK ($0.03, $0.0113/条合估值); 落盘回灌 LLMJudgeScore 全合法; 键名映射零残留; cold-start preference_match=None ✅。见 worklog 「judge_runner 写好 + smoke 验通」段。
  - 偏离: gemini 副评用裸 Files API **未接 context cache** — 副评仅 κ 抽样子集 (量小), cache 收益小 (cache 对评全量才划算, 但全量是 Doubao 不是 gemini); 故不接。
- [x] κ/QWK 一致性 pilot **完整三角跑通 + 诊断** (design §4.3 Step2): 抽 120 条 → 主 Doubao + 副 Gemini(87)/Kimi(29) → κ 三角 + 分布诊断。preference_match 两组达标 (0.78/0.84 ✅) 自证方法学; 其余低 κ 经诊断 = **Doubao 主评天花板** (readability 87/87 全 5, 跨两副评退化→定位到主评), 非不一致; safety misleading Doubao 比 Gemini 宽松 7× (判 1 vs 7)。
  - 证据: `src/kappa_sample.py`(120) + `src/kappa_compute.py`(手写 Cohen κ+QWK); 完整三角表 + 铁证诊断见 worklog 「完整 κ 三角」段 + doc §4.3 Step2 W3 pilot 段。
  - follow-up (已做): refine judge_prompt anchor (video_relevance/content_fidelity 抬门槛) → 重跑 κ + **人工裁决 72 条** (评测台盲评) 落槌: 两维 refine 都保留, video_relevance bias +0.10 (校准成功)、content_fidelity bias +0.45 (待 -0.45 校正)。judge_prompt 定稿。证据: `notes/refine_human_ratings.json` + worklog 2026-06-18 + doc §4.4「judge 校准与误差分解」。
- [x] 产品化: 通用人工评测台 `web/index.html` (两模式 tab) — refine 裁决(盲评+1-5+揭示judge对比+统计建议) + 全量抽检(13维, ground-truth锚)。data_refine.js(72) + data_audit.js(104)。
  - 证据: `web/index.html` + `web/data_refine.js`(72条) + `web/data_audit.js`(104条); preview server (localhost:8765) 截图验证两模式渲染/打分/存储/计数全过 (refine 1-5全档盲评揭示 + audit 13维 safety toggle/effect 1-5); 用户实评 72 条 → `notes/refine_human_ratings.json`; worklog 2026-06-15「通用人工评测台」段。
- [x] judge runner 存 why (可解释性): `extract_whys` 旁路存每维理由, 全量主评 16,224 条带 judge 打分依据。
  - 证据: `src/judge_runner.py` `extract_whys()` + judge_one 落盘加 `whys` 字段; 单测验键名映射(faithfulness→content_fidelity 等)全过 (本会话 Bash); worklog 2026-06-18「judge 可解释性」段。
- [x] **全量主评执行方式改 URL+TOS** (Doubao 家用美国网络不通诊断后, user 拍板去公司跑): judge_doubao 视频走 TOS 公网 URL — 绕过内联体积上限(b64~70MB, 最大3视频413) + 不每条重传(内联要356GB); 公司网络下 TOS 上传快 + Doubao 北京内网拉快。代码就绪 (家用验过 import/URL构造/inline回退), 全量待去公司跑。
  - 证据: `src/upload_tos.py` + `doubao_judge_probe.doubao_video_url()`(默认url/env切inline) + `ops/run_at_office.md`(runbook); 诊断+决策+合规边界见 worklog 2026-06-19; CLAUDE.md §2 加执行环境/内容红线区分。
- [x] 全量主评跑完: **16,185/16,185 条质量分 + whys** (judge1 填满; judge2 副评抽样 = κ pilot 120 已有), 总 $199.41, QA 全过 (重复0/score合法/whys 0缺)
  - 证据: `results/judge_main_full.jsonl` + judge_qa 全绿 (worklog 2026-06-29); 11 天运维实录 (欠费×6/worker卡死/网络墙→公司内联+压缩3大视频) 见 worklog 06-19~24
- [x] W4 聚合 + 统计分析 (两个研究问题出答案): 排名 gpt>kimi≈deepseek>gemini (Wilcoxon+Bonferroni, @A 5/6 对显著); 模态 A→B→C 单调正显著、**C→D 负** (kimi −0.042 显著+bootstrap稳健) + 维度分解 (完整视频挤占指令/persona 遵循); 合规门 95% 全过/98% 掉 4; Pareto 非支配 = gpt|C / kimi|C(甜点) / kimi|A
  - 证据: `src/aggregate.py` + `src/analyze_stats.py` (手写 Wilcoxon/bootstrap) + `results/arm_summary.json`; 全文见 worklog 2026-07-03
- [x] baseline B0/B1 判分 **832/832 完成** ($10.21): B0=3.731 / B1=3.764 (n=416 各齐); **13/13 LLM arm 显著 > B0(Δ+.43~.57) 和 B1(Δ+.40~.54)** → §4.4 最强档「LLM 值得部署」坐实; dashboard baseline 区块已点亮
  - 证据: `results/judge_baseline.jsonl`(827) + `web/data_results.js` baseline 段; 价值层次 引入LLM≫选模型≫选模态; worklog 2026-07-07
- [x] **L1 结果 Dashboard v1** (`web/dashboard.html`): 核心结论/排名点图(双口径+显著性)/模态阶梯/C→D分解/Pareto+合规门/baseline占位/下钻(原文+13维+judge理由)/口径限制; 纯静态零依赖, 数据更新重跑 `src/gen_dashboard_data.py`
  - 证据: preview 验证 10 区块全渲染+交互 OK+零 console 错+无横滚 (worklog 2026-07-03 晚)
- [~] ~~人工抽检 audit 104 条~~ — **未执行 (owner 2026-09-26 拍板收尾)**: 评测台与 104 条分层子集均已就绪 (`web/index.html` audit 模式 + `web/data_audit.js`), owner 时间预算不足, 仅零星评几条未导出 (n 太小无统计意义, 不采用)。**影响**: ① refine 泛化未经独立样本验证 → bias 估计精度受影响, 但偏差校正是常数平移、**不改 arm 间相对序** ② 全量 safety 缺人工兜底 → 安全率为 LLM-judge 口径。已如实写入 doc §4.3 Step 4 执行状态表 / §4.4 两处 / §5.5 caveat / 附录 A / README Known limitations。补齐: 跑完 104 条 (~2-3h) 即闭合 ①
- [~] ~~人工抽检 2-3% (~330-500 条)~~ — 同上未执行; v0.2 连同「2 独立评分员 + 冲突仲裁」一起升级 (doc 附录 A)

- W4 plan: gap analysis (per-arm 比, 严禁跨 arm 平均; DeepSeek 只 A/C) + scenario value 估算 + report.md 整合

---

## W4 收尾 (2026-09-26, owner 拍板结项)

- [x] **doc §5.5 实测执行结果** — 按 §5 三层规则判定 13 个 arm: 5.1 方向判断 13/13 过 → 5.2 候选筛选仅 3 个过 (**10 个全因长度合规 <90% 淘汰, 无一因安全或效果**) → 5.3 Primary = **gpt-5.5|A**; §5.4 推荐模板填实 (含 kimi-k2.6|C 落选回炉建议)
  - 证据: `src/decision_rules.py` (阈值全部取自 doc 明文, 不另发明) + `results/decision.json`; doc §5.5
- [x] **人工抽检口径收口** (没兑现的设计承诺改成实际状态, §8 产物闸) — doc §4.3 Step 4 加执行状态表(含影响/补齐路径) + §4.4 校准动作/refine泛化 两处加执行状态 + §4.1 校准锚/保险 两处加指针 + 附录 A 那条改准确 + README Known limitations
- [x] **README 全面更新** — 从「W2 complete」更新到 v0.1 完成: 新增 Key findings 4 条 (含部署推荐 + kimi|C 回炉提示) / pipeline 补齐至 8 步 (judge → baseline → 分析 → 决策 → dashboard) / Known limitations / Layout 重写
  - 证据: 合规扫描 0 命中; 引用的 12 个文件路径全部核对存在
- [x] **Dashboard 同步 §5 结论** — 推荐卡 `kimi|C` → `gpt-5.5|A` (决策规则口径, 非纯 Pareto 口径) + 新增「§5 决策规则三层漏斗」区块
  - 证据: `web/dashboard.html` + `gen_dashboard_data.py` 接 `decision.json`; 静态验证 JS 语法 ✅ / 决策区块字段全匹配 ✅ / HTTP 200 ✅
- [ ] **reviewer pass** — 找 AI 圈 peer 30 min review (owner 侧, 我做不了)
