# Runbook — 去公司跑 Doubao 全量主评（URL + TOS 方式）

> **为什么这样跑**：Doubao judge 在家用美国网络不通（上传墙 + 大视频 413 内联体积上限 + 防火墙拉取被挡）；
> 公司网络 + 视频走 TOS URL 可跑。实测依据见 `notes/worklog.md`（2026-06-19）。
> **原理**：32 个视频先传 TOS（一次），Doubao 服务端从北京内网拉 URL → 绕过 413 + 不每条重传（内联要 ~356GB）。

## 前置确认
- [ ] 在公司网络（到 `ark.cn-beijing.volces.com` + `tos-cn-beijing` 快）
- [ ] `.env` 有三把 key：`DOUBAO_API_KEY` / `VOLC_ACCESS_KEY` / `VOLC_SECRET_KEY`
- [ ] TOS 桶 `push-eval-videos` 存在，权限 = **公共读**（Doubao 免签名拉取）

## Step 1 — 传 32 视频到 TOS（一次性 ~800MB，幂等可续传）
```bash
PYTHONPATH=src .venv/bin/python src/upload_tos.py --verify
```
期望末尾：`桶内 32/32 ✅ 齐` + `Doubao 拉取+理解 成功`。没齐就重跑（自动续传已传的跳过）。

## Step 2 — reality-probe（2 条端到端，验 URL 链路 + 合法 13 维 JSON）
```bash
PYTHONPATH=src .venv/bin/python src/doubao_judge_probe.py
```
期望：`parse=OK` + effect 9 维都有分 + cold-start `pref_match=null`。

## Step 3 — 已评 383 条的处理（anchor 一致性）
`results/judge_main_full.jsonl` 已有 383 条（家用**内联**评的）。同 Doubao 同视频，URL 与内联给模型的输入一致，判分应同质 → **可保留续跑**。
最干净的做法（一种传法到底）= 清空重跑：
```bash
# 可选，想要纯 URL 一致性就执行： rm results/judge_main_full.jsonl
```

## Step 4 — 全量主评（16,224 条，分批 + 每 10% QA + 绿灯续/红灯停）
```bash
nohup bash ops/run_judge_full.sh > /dev/null 2>&1 &
```
- 进度：`tail -f /tmp/judge_full_wrapper.log`
- 每批 QA：`/tmp/judge_qa_batch*.log`（红灯 = 数据错漏 / fail 率高，wrapper 自停）
- 断点续跑：被 kill 重启**同一条命令**即可（已评的秒跳过）
- ⚠️ 开盖前先看进度数字在不在涨，涨就别重启（防双跑）

## 跑完回家做（不依赖公司网络）
- 校准：human(抽检 104) vs judge1 → 偏差校正（含 content_fidelity −0.45）+ 验 refine 泛化
- 聚合 + 显著性 → 模型排名 + 模态边际 → L1 结果 Dashboard
