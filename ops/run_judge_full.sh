#!/bin/bash
# 全量主评分批 wrapper: Doubao 评 16,224 条, 每 10% 跑 judge_qa, 绿灯自动续 / 红灯停。
# resume 断点续跑 (被 kill 重启本脚本即可, 已评的秒跳过)。
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
OUT=results/judge_main_full.jsonl
INPUTS="results/var_A_raw.jsonl results/var_B_raw.jsonl results/var_C_raw.jsonl results/var_D_raw.jsonl"
TOTAL=16224
BATCH=1623          # 10 批 (1623×10 > 16224, 末批封顶)
WORKERS=4          # 8→4 (2026-06-26 网络此刻差致 worker 卡死, 降并发减挤 + 电脑更轻)
LOG=/tmp/judge_full_wrapper.log
# 防双跑: 已有别的本脚本实例在跑 → 本实例退出 (避免两 wrapper 并发写同文件产生重复)
if [ "$(pgrep -f run_judge_full.sh | wc -l)" -gt 1 ]; then
  echo "$(date) 已有 wrapper 在跑, 本实例退出 (防双跑)" >> "$LOG"; exit 0
fi
echo "$(date) ===== 全量主评 wrapper 启动 (workers=$WORKERS) =====" >> "$LOG"
for k in $(seq 1 10); do
  LIMIT=$((k * BATCH)); [ "$LIMIT" -gt "$TOTAL" ] && LIMIT=$TOTAL
  echo "$(date) --- 批 $k: resume 评到 $LIMIT ---" >> "$LOG"
  env PYTHONUNBUFFERED=1 PYTHONPATH=src DOUBAO_VIDEO_MODE=inline caffeinate -i .venv/bin/python src/judge_runner.py \
    --mode main --inputs $INPUTS --out "$OUT" --workers "$WORKERS" --limit "$LIMIT" --sort-size \
    >> /tmp/judge_full_run.log 2>&1
  # 这批 check
  PYTHONPATH=src .venv/bin/python src/judge_qa.py "$OUT" --expect-total $TOTAL > "/tmp/judge_qa_batch$k.log" 2>&1
  rc=$?
  done_n=$(wc -l < "$OUT" 2>/dev/null || echo 0)
  if [ "$rc" -ne 0 ]; then
    echo "$(date) 批 $k 🔴 红灯(数据错漏)! 停 (已落 $done_n)。详见 /tmp/judge_qa_batch$k.log" >> "$LOG"
    exit 1
  fi
  if [ "$done_n" -lt $((LIMIT * 9 / 10)) ]; then
    echo "$(date) 批 $k 🔴 只评到 $done_n/$LIMIT (<90%), fail 率高(疑余额/网络), 停" >> "$LOG"
    exit 2
  fi
  echo "$(date) 批 $k 🟢 绿灯 ($done_n/$TOTAL), 续下一批" >> "$LOG"
done
echo "$(date) ===== 全量主评完成 16224/16224 =====" >> "$LOG"
