#!/usr/bin/env bash
# 部署 walle-agent：检查配置 -> 安装到系统目录 -> 安装 systemd 服务 -> 提示启停命令
# 用法：bash scripts/deploy.sh（普通用户执行，脚本内部会调用 sudo）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$ROOT/etc/walle-agent.conf"
UNIT="$ROOT/etc/walle-agent.service"
GEN="$ROOT/scripts/auto_gen_conf_service.sh"

CONF_DIR="/etc/walle-ai"
CONF_DST="$CONF_DIR/walle-agent.conf"
UNIT_DIR="/etc/systemd/system"
UNIT_DST="$UNIT_DIR/walle-agent.service"

info() { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] 失败: %s\n' "$*" >&2; exit 1; }

# ---------- 1. 检查生成的文件 ----------
info "1/3 检查配置与服务文件"
if [ ! -s "$CONF" ] || [ ! -s "$UNIT" ]; then
  printf '[deploy] 失败: 缺少以下文件：\n' >&2
  [ -s "$CONF" ] || printf '         %s\n' "$CONF" >&2
  [ -s "$UNIT" ] || printf '         %s\n' "$UNIT" >&2
  printf '         请先执行：bash %s\n' "$GEN" >&2
  exit 1
fi
grep -q '^EnvironmentFile=' "$UNIT" \
  || fail "$UNIT 缺少 EnvironmentFile= 行，请重新执行 bash $GEN"
info "    $CONF"
info "    $UNIT"

# ---------- 2. 安装到系统目录 ----------
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  fail "安装到系统目录需要 root 权限，请用 sudo 执行本脚本"
fi

info "2/3 安装配置与 systemd 单元"
$SUDO install -d -m 0755 "$CONF_DIR"
$SUDO install -d -m 0755 "$UNIT_DIR"
$SUDO install -m 0600 -o root -g root "$CONF" "$CONF_DST"

# 单元里的 EnvironmentFile 改为系统配置路径，避免继续引用仓库内文件
unit_tmp="$(mktemp)"
trap 'rm -f "$unit_tmp"' EXIT
sed -E "s|^EnvironmentFile=.*|EnvironmentFile=$CONF_DST|" "$UNIT" > "$unit_tmp"
$SUDO install -m 0644 -o root -g root "$unit_tmp" "$UNIT_DST"
$SUDO systemctl daemon-reload 2>/dev/null || info "    提示：systemctl daemon-reload 未成功，非 systemd 环境可忽略"

# ---------- 3. 提示必要信息 ----------
info "3/3 部署完成"
cat <<EOF

================= walle-agent 部署完成 =================
配置文件    : $CONF_DST（600，root:root）
服务单元    : $UNIT_DST
仓库内文件  : $CONF、$UNIT（保留，start_agent.sh 可继续手动启动）

启动服务：
  sudo systemctl start walle-agent

开机自启（可选）：
  sudo systemctl enable walle-agent

查看状态：
  systemctl status walle-agent --no-pager

查看日志：
  journalctl -u walle-agent -f
  本地日志：$ROOT/log/car_agent.log

注意：服务启动前请停止手动运行的 agent（python3 $ROOT/src/car_agent.py），
      否则同一 clientid 会互相顶掉。
======================================================
EOF

running="$(pgrep -f "$ROOT/src/car_agent.py" || true)"
if [ -n "$running" ]; then
  info "检测到正在运行的 agent 进程（PID: $(echo "$running" | tr '\n' ' ')），启动服务前请先停止。"
fi
