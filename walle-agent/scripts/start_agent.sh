#!/usr/bin/env bash
# 手动启动 walle-agent：纯 MQTT 常驻进程，不监听任何端口。
# 默认安静；调试报关需显式：WALLE_AGENT_DEBUG=1 bash scripts/start_agent.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$ROOT/etc/walle-agent.conf"
[ -f "$CONF" ] || { echo "缺少 $CONF，请先运行 scripts/auto_gen_conf_service.sh"; exit 1; }

cd "$ROOT"
if [ -n "${WALLE_AGENT_DEBUG:-}" ]; then
  echo "[walle-agent] 调试模式：打印所有 MQTT 报文"
  export WALLE_AGENT_DEBUG
fi
exec python3 "$ROOT/src/car_agent.py"
