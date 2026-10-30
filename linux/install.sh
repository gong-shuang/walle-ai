#!/usr/bin/env bash
# ============================================================================
# 树莓派监控 Agent —— 一键安装 / 升级
#
#   sudo ./install.sh                 交互式首次安装（会提示必填项）
#   sudo ./install.sh --update        只更新代码并重启（沿用已有配置）
#   sudo ./install.sh --dry-run       打印将要执行的动作，不落盘
#   sudo ./install.sh --skip-deps     不执行 apt 安装依赖
#   sudo ./install.sh -h              帮助
#
# 非交互（批量部署）：
#   MQTT_HOST=... MQTT_PORT=8883 MQTT_USERNAME=... MQTT_PASSWORD=... \
#   MQTT_TENANT=home sudo -E ./install.sh
# ============================================================================
set -euo pipefail

APP_NAME="rpi-monitor"
ENV_FILE="/etc/rpi-monitor.env"
UNIT_FILE="/etc/systemd/system/${APP_NAME}.service"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="install"
DRY_RUN=0
SKIP_DEPS=0

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

for arg in "$@"; do
  case "$arg" in
    --update) MODE="update" ;;
    --dry-run) DRY_RUN=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    -h|--help) usage ;;
    *) echo "未知参数: $arg（-h 查看帮助）" >&2; exit 2 ;;
  esac
done

# ------------------------------------------------------------------ 输出辅助
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi
step() { echo; echo "${BOLD}==> $*${RESET}"; }
ok()   { echo "    ${GREEN}✓${RESET} $*"; }
warn() { echo "    ${YELLOW}!${RESET} $*"; }
die()  { echo "${RED}✗ $*${RESET}" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then echo "    ${DIM}[dry-run] $*${RESET}"; else "$@"; fi; }

# ------------------------------------------------------------------ 前置检查
if [ "$DRY_RUN" = 1 ]; then
  [ "$(uname -s)" = "Linux" ] || warn "非 Linux 环境：dry-run 只演示流程，不执行系统命令"
  [ "$(id -u)" = "0" ] || warn "非 root：dry-run 无需提权"
else
  [ "$(uname -s)" = "Linux" ] || die "这个脚本只能在树莓派（Linux）上运行"
  [ "$(id -u)" = "0" ] || die "请用 sudo 运行：sudo ./install.sh"
fi

TARGET_USER="${SUDO_USER:-root}"
[ "$TARGET_USER" = "root" ] && warn "SUDO_USER 为空，服务将安装到 root 用户下"
DEFAULT_DIR="/home/${TARGET_USER}/mqtt_demo"
[ "$TARGET_USER" = "root" ] && DEFAULT_DIR="/opt/${APP_NAME}"

echo "${BOLD}树莓派监控 Agent 部署${RESET}"
echo "  模式      : $MODE"
echo "  源码目录  : $SRC_DIR"
echo "  目标用户  : $TARGET_USER"
[ "$DRY_RUN" = 1 ] && echo "  ${YELLOW}dry-run：不会修改任何文件${RESET}"

[ -f "$SRC_DIR/rpi_monitor.py" ] || die "找不到 $SRC_DIR/rpi_monitor.py（请在 linux/ 目录内运行）"

# ------------------------------------------------------------------ 读取已有配置（只认白名单键，不 source）
MQTT_HOST="${MQTT_HOST:-}"
MQTT_PORT="${MQTT_PORT:-}"
MQTT_USERNAME="${MQTT_USERNAME:-}"
MQTT_PASSWORD="${MQTT_PASSWORD:-}"
MQTT_TENANT="${MQTT_TENANT:-}"
MQTT_TLS="${MQTT_TLS:-auto}"
MQTT_CA_FILE="${MQTT_CA_FILE:-}"
INSTALL_DIR="${INSTALL_DIR:-}"

if [ -f "$ENV_FILE" ]; then
  ok "发现已有配置：$ENV_FILE（作为默认值）"
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    case "$k" in
      MQTT_HOST|MQTT_PORT|MQTT_USERNAME|MQTT_PASSWORD|MQTT_TENANT|MQTT_TLS|MQTT_CA_FILE)
        # 命令行/环境变量优先，不被文件覆盖
        if [ -z "$(eval "printf '%s' \"\${$k}\"")" ]; then
          printf -v "$k" '%s' "$v"
        fi
        ;;
    esac
  done < "$ENV_FILE"
fi

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"

# ------------------------------------------------------------------ 交互式收集必填项
mask() { [ -n "$1" ] && printf '********' || true; }

# ask 变量名 提示 默认值 是否必填 是否密码
ask() {
  local var="$1" prompt="$2" default="${3:-}" required="${4:-no}" secret="${5:-no}"
  local current
  current="$(eval "printf '%s' \"\${$var}\"")"
  [ -z "$current" ] && current="$default"
  local shown="$current"
  [ "$secret" = "yes" ] && shown="$(mask "$current")"
  local hint=""
  [ -n "$shown" ] && hint=" [当前: $shown]"
  [ "$required" = "yes" ] && hint="$hint ${RED}*必填${RESET}"

  if [ ! -t 0 ]; then                      # 非交互：用已有值/默认值
    printf -v "$var" '%s' "$current"
    return
  fi

  local input=""
  while true; do
    if [ "$secret" = "yes" ]; then
      read -rsp "  $prompt$hint: " input; echo
    else
      read -rp  "  $prompt$hint: " input
    fi
    [ -z "$input" ] && input="$current"
    if [ "$required" = "yes" ] && [ -z "$input" ]; then
      echo "    ${RED}这一项必填${RESET}"; continue
    fi
    break
  done
  printf -v "$var" '%s' "$input"
}

if [ "$MODE" = "install" ]; then
  step "必填项（回车使用中括号里的默认值）"
  echo "  ${DIM}MQTT_TENANT 必须与手机 App 里填的「MQTT 用户名」完全一致${RESET}"
  ask MQTT_HOST     "MQTT Broker 地址"           "www.baidong.xyz" yes
  ask MQTT_PORT     "端口（8883=TLS，1883=明文）" "8883"            yes
  ask MQTT_USERNAME "设备 MQTT 用户名"            ""                yes
  ask MQTT_PASSWORD "设备 MQTT 密码"              ""                yes yes
  ask MQTT_TENANT   "租户（= 手机 App 的用户名）" ""                yes
  ask INSTALL_DIR   "安装目录"                    "$DEFAULT_DIR"    no
else
  step "升级模式：沿用 $ENV_FILE 中的配置"
  [ -n "$MQTT_HOST" ] || die "缺少 MQTT_HOST，请先跑一次交互式安装"
  [ -n "$MQTT_USERNAME" ] || die "缺少 MQTT_USERNAME"
  [ -n "$MQTT_TENANT" ] || die "缺少 MQTT_TENANT"
  [ -n "$MQTT_PASSWORD" ] || warn "未读到 MQTT_PASSWORD（若用证书免密可忽略）"
fi

# ------------------------------------------------------------------ 校验
step "参数校验"
if [ "$MODE" = "install" ]; then
  for pair in "MQTT_HOST:Broker 地址" "MQTT_USERNAME:设备用户名" \
              "MQTT_PASSWORD:设备密码" "MQTT_TENANT:租户名"; do
    k="${pair%%:*}"; label="${pair#*:}"
    [ -n "$(eval "printf '%s' \"\${$k}\"")" ] || die "缺少必填项：$label（$k）"
  done
fi
case "$MQTT_PORT" in
  ''|*[!0-9]*) die "端口必须是数字：$MQTT_PORT" ;;
esac
[ "$MQTT_PORT" -ge 1 ] && [ "$MQTT_PORT" -le 65535 ] || die "端口超出 1-65535"
ok "端口 $MQTT_PORT"

if [ "$MQTT_PORT" = "1883" ]; then
  warn "1883 是明文端口，凭据与状态会在网络上裸奔；建议在 EMQX 开 TLS 后用 8883"
fi
case "$MQTT_TENANT" in
  *[!A-Za-z0-9_-]*) warn "租户含特殊字符，会被清洗成下划线（App 端同样处理）" ;;
esac
[ -n "$MQTT_TENANT" ] || die "租户不能为空"

# ------------------------------------------------------------------ 系统依赖
if [ "$SKIP_DEPS" = 0 ] && [ "$MODE" = "install" ]; then
  step "安装系统依赖（python3-venv / v4l-utils / ffmpeg）"
  if [ "$DRY_RUN" = 1 ]; then
    echo "    ${DIM}[dry-run] apt-get install -y python3-venv v4l-utils ffmpeg${RESET}"
  else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq || warn "apt-get update 失败，继续"
    if apt-get install -y -qq python3-venv v4l-utils ffmpeg >/dev/null 2>&1; then
      ok "依赖已就绪"
    else
      warn "部分依赖安装失败（摄像头 / 推流功能可能不可用）"
    fi
  fi
fi

# ------------------------------------------------------------------ 拷贝代码
step "部署代码到 $INSTALL_DIR"
run mkdir -p "$INSTALL_DIR"
for f in rpi_monitor.py selftest_topics.py requirements.txt; do
  run cp -f "$SRC_DIR/$f" "$INSTALL_DIR/$f"
  [ "$DRY_RUN" = 1 ] || chown "$TARGET_USER":"$TARGET_USER" "$INSTALL_DIR/$f"
done
ok "已拷贝 rpi_monitor.py / selftest_topics.py / requirements.txt"

# ------------------------------------------------------------------ 虚拟环境
step "准备 Python 虚拟环境"
if [ -x "$INSTALL_DIR/.venv/bin/python" ] && [ "$DRY_RUN" = 0 ]; then
  ok "已存在 .venv，跳过创建"
else
  run python3 -m venv "$INSTALL_DIR/.venv" || die "创建 venv 失败（是否安装了 python3-venv？）"
fi
run "$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
run "$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
ok "依赖安装完成（paho-mqtt / psutil）"

# ------------------------------------------------------------------ 配置落盘
step "写入配置 $ENV_FILE"
ENV_CONTENT="# 由 install.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')
MQTT_HOST=${MQTT_HOST}
MQTT_PORT=${MQTT_PORT}
MQTT_USERNAME=${MQTT_USERNAME}
MQTT_PASSWORD=${MQTT_PASSWORD}
MQTT_TENANT=${MQTT_TENANT}
MQTT_TLS=${MQTT_TLS:-auto}
MQTT_CA_FILE=${MQTT_CA_FILE:-}"
if [ "$DRY_RUN" = 1 ]; then
  echo "    ${DIM}[dry-run] 将写入：${RESET}"
  printf '%s\n' "$ENV_CONTENT" | sed 's/^/      /'
else
  printf '%s\n' "$ENV_CONTENT" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
  ok "已写入并设为 600（仅 root 可读）"
fi

# ------------------------------------------------------------------ systemd
step "安装 systemd 服务"
UNIT_CONTENT="[Unit]
Description=RaspberryPi System Monitor over MQTT
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/rpi_monitor.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target"
if [ "$DRY_RUN" = 1 ]; then
  echo "    ${DIM}[dry-run] 将写入 $UNIT_FILE 并执行 systemctl enable --now $APP_NAME${RESET}"
else
  printf '%s\n' "$UNIT_CONTENT" > "$UNIT_FILE"
  systemctl daemon-reload
  systemctl enable "$APP_NAME" >/dev/null 2>&1
  systemctl restart "$APP_NAME"
  ok "服务已启动：$APP_NAME"
fi

# ------------------------------------------------------------------ 自检
step "协议自检（不需要连 broker）"
if [ "$DRY_RUN" = 1 ]; then
  echo "    ${DIM}[dry-run] python3 selftest_topics.py --tenant $MQTT_TENANT${RESET}"
else
  if "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/selftest_topics.py" \
       --tenant "$MQTT_TENANT" >/tmp/rpi-selftest.log 2>&1; then
    ok "自检通过（详细输出：/tmp/rpi-selftest.log）"
  else
    warn "自检失败，看 /tmp/rpi-selftest.log"
  fi
fi

# ------------------------------------------------------------------ 完成
step "完成"
if [ "$DRY_RUN" = 0 ]; then
  sleep 2
  echo "  最近日志："
  journalctl -u "$APP_NAME" -n 12 --no-pager 2>/dev/null | sed 's/^/      /' || true
fi

DEVICE_ID=""
if [ "$DRY_RUN" = 0 ]; then
  DEVICE_ID="$(journalctl -u "$APP_NAME" -n 200 --no-pager 2>/dev/null \
    | grep -o 'device_id: [A-Za-z0-9_-]*' | tail -1 | awk '{print $2}')" || true
fi
[ -z "$DEVICE_ID" ] && DEVICE_ID="<启动后执行 journalctl -u $APP_NAME | grep device_id 获取>"

cat <<EOF

  ${BOLD}接下来${RESET}
  1) 看实时日志：      sudo journalctl -u ${APP_NAME} -f
  2) 设备主题前缀：    walle/${MQTT_TENANT}/${DEVICE_ID}/
  3) 改配置：          sudo nano ${ENV_FILE} && sudo systemctl restart ${APP_NAME}
  4) 手机 App 填写：   Broker=${MQTT_HOST}  端口=${MQTT_PORT}
                      用户名=${MQTT_TENANT}  密码=<手机账号口令>
                      ${DIM}（租户 = 用户名，App 自动订阅 walle/${MQTT_TENANT}/#）${RESET}
  5) EMQX 授权：      见 linux/emqx/acl.conf.example
EOF
