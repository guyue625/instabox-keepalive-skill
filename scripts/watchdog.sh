#!/usr/bin/env bash
# watchdog.sh — 放在 InstaBox 机器里的 /data/watchdog.sh（chmod +x）。
# 外部 tick 每 10 分钟、以及每次轮换开机后通过 exec 以 sudo 调用它：既算活跃，又把服务拉回来。
# 服务要写成 systemd unit 并 enable（Restart=always），开机会自己起；这里是第二道保险。
# 退出码非 0 时 tick 会在日志里记一行并在 1 分钟后重试。
set -u
SERVICES=()   # 改成你的服务名，例如 SERVICES=(sing-box myapp)

if [ "${#SERVICES[@]}" -eq 0 ]; then
  # 没填服务时只刷活跃——在输出里明确写一行，避免误以为看门狗在守护服务。
  # tick 只看退出码：这里仍退出 0，不触发无意义的每分钟重试。
  echo "watchdog: SERVICES 为空，未检查任何服务（只刷活跃）。要守护服务请编辑 /data/watchdog.sh 的 SERVICES。"
  date
  exit 0
fi

rc=0
for s in "${SERVICES[@]}"; do
  systemctl is-active --quiet "$s" && continue
  systemctl start "$s" && echo "已拉起 $s" || { echo "拉起失败 $s" >&2; rc=1; }
done
date
exit $rc
