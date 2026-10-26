#!/usr/bin/env bash
# loop.sh — InstaBox 保活常驻 loop，零 token。给每个账号跑一拍 instabox_tick.py，然后睡到最早到期的机器
# （最多 60 秒）。没到期的账号不调 API；平时约每 10 分钟一次，只在冷却结束前后变成每 5 秒。
# 部署时改 CREDS（每个账号一个凭证名）。必须用绝对路径启动，keepalive 靠 cmdline 认进程。
#
# 修复（fork 版）：
# - CREDS 默认只留一个示例凭证，不再带着占位账号一起跑（占位账号每轮都会凭证报错）。
#   多账号就在括号里继续加，例如 CREDS=(custom.instabox custom.instabox-work)。
# - 睡眠下限从 1 秒抬到 5 秒，且 next_wake 为空/不是数字/已过期时默认睡 60 秒，
#   绝不每 1 秒空转重试。
# - tick 运行期间另起一个心跳刷新子进程（每 30 秒写一次 heartbeat）：
#   单账号多台机器串行轮换/exec 可能跑好几分钟，心跳不能只靠每账号开始时写一次，
#   否则会被 keepalive 按 20 分钟阈值误判（长批次下甚至单轮就可能很久）。
set -u
ROOT="$HOME/workspace/instabox-keepalive"
CREDS=(custom.instabox)
LOG="$ROOT/loop.log"
mkdir -p "$ROOT"
echo $$ > "$ROOT/loop.pid"

if [ "${#CREDS[@]}" -eq 0 ]; then
  echo "$(date '+%F %T') CREDS 为空：请在 loop.sh 里至少填一个凭证名" >> "$LOG"
  exit 1
fi

run_tick() {
  local cred="$1" hb_pid=""
  ( while true; do sleep 30; date +%s > "$ROOT/heartbeat"; done ) &
  hb_pid=$!
  INSTABOX_ROOT="$ROOT" python3 "$ROOT/instabox_tick.py" "$cred" >> "$LOG" 2>&1
  kill "$hb_pid" 2>/dev/null || true
  wait "$hb_pid" 2>/dev/null || true
}

while true; do
  for c in "${CREDS[@]}"; do
    date +%s > "$ROOT/heartbeat"   # 每个账号前都写；tick 期间另有子进程续写
    run_tick "$c"
  done
  # 只在有动作或出错时才写日志；留最近 200 行
  [ -f "$LOG" ] || : > "$LOG"
  [ "$(wc -l < "$LOG")" -gt 200 ] && tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  wake=$(cat "$ROOT/next_wake" 2>/dev/null)
  case "$wake" in ''|*[!0-9]*) wake=0 ;; esac   # 空/非法一律当作无有效到期时间
  now=$(date +%s)
  if [ "$wake" -le 0 ] || [ "$wake" -le "$now" ]; then
    s=60                                        # 无有效到期：睡满一拍，不忙循环
  else
    s=$(( wake - now )); [ "$s" -gt 60 ] && s=60; [ "$s" -lt 5 ] && s=5
  fi
  sleep "$s"
done
