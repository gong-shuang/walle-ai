#!/usr/bin/env bash
# 交互式生成 walle-agent 的配置文件与 systemd 服务文件。
# 用法：bash scripts/auto_gen_conf_service.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$ROOT/etc/walle-agent.conf"
UNIT="$ROOT/etc/walle-agent.service"

ask() { # ask <变量名> <提示> [silent]
  local __var="$1" __prompt="$2" __silent="${3:-}" __value=""
  while [ -z "$__value" ]; do
    if [ "$__silent" = "silent" ]; then
      read -r -s -p "$__prompt" __value; echo
    else
      read -r -p "$__prompt" __value
    fi
    [ -n "$__value" ] || echo "  不能为空，请重新输入。"
  done
  printf -v "$__var" '%s' "$__value"
}

echo "===== walle-agent 配置生成 ====="
if [ -f "$CONF" ] || [ -f "$UNIT" ]; then
  echo "检测到已存在："
  [ -f "$CONF" ] && echo "  - $CONF"
  [ -f "$UNIT" ] && echo "  - $UNIT"
  read -r -p "覆盖这两个文件？[y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) echo "已取消，未修改任何文件。"; exit 0 ;; esac
fi

while :; do
  ask MQTT_HOST "MQTT 域名/IP："
  case "$MQTT_HOST" in *[[:space:]]*) echo "  不能包含空格，请重新输入。"; MQTT_HOST="";; *) break;; esac
done

while :; do
  ask MQTT_PORT "MQTT 端口（1-65535）："
  if [[ "$MQTT_PORT" =~ ^[0-9]+$ ]] && [ "$MQTT_PORT" -ge 1 ] && [ "$MQTT_PORT" -le 65535 ]; then
    break
  fi
  echo "  端口不合法，请重新输入。"; MQTT_PORT=""
done

ask MQTT_USER "MQTT 用户名："
ask_tenant() {
  local v=""
  while :; do
    read -r -p "租户/客户侧标识 TENANT（回车默认 home；决定主题 walle/{tenant}/...，与 MQTT 用户名不是一回事）：" v || true
    v="${v:-home}"
    if [[ "$v" =~ ^[A-Za-z0-9_-]+$ ]]; then TENANT="$v"; break; fi
    echo "  只能包含字母、数字、下划线、短横线，请重新输入。"
    v=""
  done
}

ask_tenant

ask MQTT_PASSWORD "MQTT 密码（输入不回显）：" silent

if [ "$MQTT_PORT" = "8883" ]; then MQTT_TLS=1; else MQTT_TLS=0; fi
mkdir -p "$ROOT/etc"

cat > "$CONF" <<EOF
# walle-agent 配置（由 scripts/auto_gen_conf_service.sh 生成）
# MQTT 连接
MQTT_HOST=$MQTT_HOST
MQTT_PORT=$MQTT_PORT
MQTT_TLS=$MQTT_TLS
MQTT_USER=$MQTT_USER
MQTT_PASSWORD=$MQTT_PASSWORD

# 租户段：主题 walle/{tenant}/...；与 MQTT 用户名相互独立
TENANT=$TENANT
CHAT_MQTT_ENABLED=1
CLIENT_ID_PREFIX=agent-

# 运动限制与发送频率
DEFAULT_SPEED=0.5
MIN_SPEED=0.5
MAX_SPEED=0.6
MAX_DISTANCE=10
DEFAULT_ANGULAR=1.0
MAX_ANGLE=360
SEND_HZ=5

# 复杂语句走 LLM（默认关闭）
LLM_ENABLED=0
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
EOF
chmod 600 "$CONF"

cat > "$UNIT" <<EOF
[Unit]
Description=walle-agent (MQTT car control agent)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$ROOT
EnvironmentFile=$CONF
ExecStart=/usr/bin/python3 $ROOT/src/car_agent.py
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo
echo "已生成："
echo "  $CONF"
echo "  $UNIT"
echo "  tenant：$TENANT"
echo "  agent 名字：首次启动自动生成 agent_<hostname>，可在 Web 修改"
echo
echo "安装为系统服务："
echo "  sudo cp $UNIT /etc/systemd/system/walle-agent.service"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now walle-agent"
