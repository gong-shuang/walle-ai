#!/bin/bash
# 交互式生成摄像头配置 etc/walle_camera.conf（JSON）
# 依赖：v4l2-ctl（v4l-utils）、python3；可选 whiptail / dialog（无则使用文本菜单）
# 用法：./auto_gen_camera_conf.sh [--output <path>]
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
walle_home="$(cd "$script_dir/.." && pwd)"
output="$walle_home/etc/walle_camera.conf"

usage() {
    cat <<EOF
用法: $0 [--output <path>]

交互式选择摄像头、格式、分辨率、帧率并命名，生成 JSON 配置。
默认输出：$walle_home/etc/walle_camera.conf
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output)
            if [[ -z "${2:-}" ]]; then
                echo "--output 需要一个路径" >&2
                exit 1
            fi
            output="$2"
            shift 2
            ;;
        --output=*)
            output="${1#*=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ ! -t 0 || ! -t 1 ]]; then
    echo "本脚本需要交互式终端，请在终端中直接运行。" >&2
    exit 1
fi
if ! command -v v4l2-ctl >/dev/null 2>&1; then
    echo "未找到 v4l2-ctl，请先安装：sudo apt install -y v4l-utils" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3" >&2
    exit 1
fi

if command -v whiptail >/dev/null 2>&1; then
    ui="whiptail"
elif command -v dialog >/dev/null 2>&1; then
    ui="dialog"
else
    ui="plain"
fi

plain_menu() {
    local title="$1" prompt="$2"
    shift 2
    local -a tags=() descs=()
    while [[ $# -gt 0 ]]; do
        tags+=("$1")
        descs+=("${2:-}")
        shift 2
    done
    {
        echo
        echo "== $title =="
        echo "$prompt"
        local i
        for i in "${!tags[@]}"; do
            printf '  %2d) %-14s %s\n' "$((i + 1))" "${tags[$i]}" "${descs[$i]}"
        done
    } >&2
    local choice
    while true; do
        read -r -p "请输入序号 [1-${#tags[@]}]: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] &&
           ((choice >= 1 && choice <= ${#tags[@]})); then
            printf '%s\n' "${tags[$((choice - 1))]}"
            return 0
        fi
        echo "无效选择：$choice" >&2
    done
}

ui_menu() {
    local title="$1" prompt="$2"
    shift 2
    if [[ "$ui" == "whiptail" ]]; then
        whiptail --title "$title" --menu "$prompt" 20 78 12 "$@" 3>&1 1>&2 2>&3
    elif [[ "$ui" == "dialog" ]]; then
        dialog --stdout --title "$title" --menu "$prompt" 20 78 12 "$@"
    else
        plain_menu "$title" "$prompt" "$@"
    fi
}

ui_input() {
    local title="$1" prompt="$2" default="$3"
    if [[ "$ui" == "whiptail" ]]; then
        whiptail --title "$title" --inputbox "$prompt" 10 78 "$default" \
            3>&1 1>&2 2>&3
    elif [[ "$ui" == "dialog" ]]; then
        dialog --stdout --title "$title" --inputbox "$prompt" 10 78 "$default"
    else
        local value
        read -r -p "$prompt [$default]: " value
        printf '%s\n' "${value:-$default}"
    fi
}

ui_confirm() {
    local title="$1" text="$2" height="${3:-16}"
    if [[ "$ui" == "whiptail" ]]; then
        whiptail --title "$title" --yesno "$text" "$height" 78
    elif [[ "$ui" == "dialog" ]]; then
        dialog --title "$title" --yesno "$text" "$height" 78
    else
        local answer
        read -r -p "$text [Y/n]: " answer
        [[ "${answer:-Y}" =~ ^[Yy]$ ]]
    fi
}

ui_msg() {
    local title="$1" text="$2"
    if [[ "$ui" == "whiptail" ]]; then
        whiptail --title "$title" --msgbox "$text" 12 78
    elif [[ "$ui" == "dialog" ]]; then
        dialog --title "$title" --msgbox "$text" 12 78
    else
        echo "$text" >&2
    fi
}

pick() {
    local value
    if ! value="$(ui_menu "$@")"; then
        echo "已取消，未写入配置。" >&2
        exit 1
    fi
    printf '%s\n' "$value"
}

# 与 pick 相同，但菜单不显示 tag 列（只显示描述）。返回值仍是 tag。
# 适用于 tag 是内部值（如秒数）不适合展示给用户的场景。
pick_notags() {
    local title="$1" prompt="$2"
    shift 2
    local value
    if [[ "$ui" == "whiptail" ]]; then
        if ! value="$(whiptail --notags --title "$title" --menu "$prompt" \
                20 78 12 "$@" 3>&1 1>&2 2>&3)"; then
            echo "已取消，未写入配置。" >&2
            exit 1
        fi
    elif [[ "$ui" == "dialog" ]]; then
        if ! value="$(dialog --no-tags --stdout --title "$title" --menu "$prompt" \
                20 78 12 "$@")"; then
            echo "已取消，未写入配置。" >&2
            exit 1
        fi
    else
        # 纯文本：仅显示序号 + 描述，不显示 tag
        local -a tags=() descs=()
        while [[ $# -gt 0 ]]; do
            tags+=("$1"); descs+=("${2:-}"); shift 2
        done
        {
            echo
            echo "== $title =="
            echo "$prompt"
            local i
            for i in "${!tags[@]}"; do
                printf '  %2d) %s\n' "$((i + 1))" "${descs[$i]}"
            done
        } >&2
        local choice
        while true; do
            read -r -p "请输入序号 [1-${#tags[@]}]: " choice
            if [[ "$choice" =~ ^[0-9]+$ ]] &&
               ((choice >= 1 && choice <= ${#tags[@]})); then
                value="${tags[$((choice - 1))]}"
                break
            fi
            echo "无效选择：$choice" >&2
        done
    fi
    printf '%s\n' "$value"
}

ask() {
    local value
    if ! value="$(ui_input "$@")"; then
        echo "已取消，未写入配置。" >&2
        exit 1
    fi
    printf '%s\n' "$value"
}

die() {
    ui_msg "错误" "$1" || true
    echo "$1" >&2
    exit 1
}

# ---------- 1. 枚举视频设备（只保留能报告尺寸的采集节点） ----------
devices=()
for sys_node in /sys/class/video4linux/video*; do
    [[ -e "$sys_node" ]] || continue
    dev="/dev/$(basename "$sys_node")"
    [[ -e "$dev" ]] || continue
    formats_out="$(LC_ALL=C v4l2-ctl -d "$dev" --list-formats-ext 2>/dev/null || true)"
    grep -q 'Size:' <<<"$formats_out" || continue
    dev_name="$(cat "$sys_node/name" 2>/dev/null || echo unknown)"
    link="$(readlink -f "$sys_node/device" 2>/dev/null || true)"
    kind="非USB"
    [[ "$link" == *usb* ]] && kind="USB"
    devices+=("$dev" "$dev_name ($kind)")
done
if [[ ${#devices[@]} -eq 0 ]]; then
    die "未找到可用的视频采集设备。请确认摄像头已插好，并安装 v4l-utils。"
fi

device="$(pick "选择摄像头" "检测到以下视频设备：" "${devices[@]}")"

# ---------- 2. 解析格式 / 分辨率 / 帧率 ----------
formats_raw="$(LC_ALL=C v4l2-ctl -d "$device" --list-formats-ext 2>/dev/null || true)"
mapfile -t entries < <(python3 - "$formats_raw" <<'PY'
import re
import sys

text = sys.argv[1]
fmt = None
sizes = []
seen = set()


def emit(size, fps):
    key = (fmt, size[0], size[1], round(float(fps), 3))
    if key in seen:
        return
    seen.add(key)
    print("%s %d %d %.3f" % (fmt, size[0], size[1], float(fps)))


for raw in text.splitlines():
    line = raw.strip()
    m = re.match(r"\[\d+\]:\s*'([^']+)'", line)
    if m:
        fmt = m.group(1).upper()
        sizes = []
        continue
    m = re.match(
        r"Size:\s*(?:Discrete|Stepwise)\s+(\d+)x(\d+)"
        r"(?:\s*-\s*(\d+)x(\d+))?", line)
    if m:
        first = (int(m.group(1)), int(m.group(2)))
        if m.group(3):
            second = (int(m.group(3)), int(m.group(4)))
            sizes = [first] if first == second else [first, second]
        else:
            sizes = [first]
        continue
    m = re.match(
        r"Interval:\s*(?:Discrete|Stepwise)\s+[\d.]+s"
        r"(?:\s*-\s*[\d.]+s)?\s*\(([\d.]+)\s+fps"
        r"(?:\s*-\s*([\d.]+)\s+fps)?\)", line)
    if m and fmt and sizes:
        fps1 = float(m.group(1))
        fps2 = float(m.group(2)) if m.group(2) else fps1
        for size in sizes:
            emit(size, fps1)
            if fps2 != fps1:
                emit(size, fps2)
PY
)
if [[ ${#entries[@]} -eq 0 ]]; then
    die "无法从 $device 读取格式/分辨率/帧率，请用 v4l2-ctl --list-formats-ext 检查。"
fi

mapfile -t all_formats < <(
    printf '%s\n' "${entries[@]}" | awk '{print $1}' | awk '!seen[$0]++')
formats=()
for f in "${all_formats[@]}"; do
    case "$f" in
        MJPG|YUYV) formats+=("$f") ;;
    esac
done
if [[ ${#formats[@]} -eq 0 ]]; then
    die "当前仅支持 MJPG/YUYV 格式，该设备只报告：${all_formats[*]}"
fi
# MJPG 优先（压缩采集 + MPP 硬件解码，USB 带宽更低）
ordered=()
for f in "${formats[@]}"; do [[ "$f" == "MJPG" ]] && ordered+=("$f"); done
for f in "${formats[@]}"; do [[ "$f" == "MJPG" ]] || ordered+=("$f"); done
formats=("${ordered[@]}")

menu=()
for f in "${formats[@]}"; do
    case "$f" in
        MJPG) menu+=("$f" "MJPG（压缩采集，MPP 硬件解码，推荐）") ;;
        YUYV) menu+=("$f" "YUYV（原始 YUV，USB 带宽占用更高）") ;;
    esac
done
format="$(pick "选择格式" "$device 支持的采集格式：" "${menu[@]}")"

mapfile -t resolutions < <(
    printf '%s\n' "${entries[@]}" |
        awk -v f="$format" '$1 == f {print $2 "x" $3}' | awk '!seen[$0]++')
if [[ ${#resolutions[@]} -eq 0 ]]; then
    die "$device 的 $format 格式没有可用分辨率。"
fi

# 同一页面：左列分辨率，右列该分辨率支持的帧率
menu=()
for r in "${resolutions[@]}"; do
    mapfile -t rates_for_res < <(
        printf '%s\n' "${entries[@]}" |
            awk -v f="$format" -v r="$r" \
                '$1 == f && $2 "x" $3 == r {printf "%g\n", $4}' |
            awk '!seen[$0]++')
    fps_text=""
    for rate in "${rates_for_res[@]}"; do
        if [[ -z "$fps_text" ]]; then
            fps_text="$rate"
        else
            fps_text="$fps_text / $rate"
        fi
    done
    menu+=("$r" "${fps_text} fps")
done
resolution="$(pick "选择分辨率与帧率" \
    "$device / $format 支持的分辨率与帧率：" "${menu[@]}")"

mapfile -t rates < <(
    printf '%s\n' "${entries[@]}" |
        awk -v f="$format" -v r="$resolution" \
            '$1 == f && $2 "x" $3 == r {printf "%g\n", $4}' |
        awk '!seen[$0]++')
if [[ ${#rates[@]} -eq 0 ]]; then
    die "$device 的 $format $resolution 没有可用帧率。"
fi
if [[ ${#rates[@]} -eq 1 ]]; then
    fps="${rates[0]}"
else
    # 少数摄像头同一分辨率支持多档帧率，此时再补一页选择
    menu=()
    for rate in "${rates[@]}"; do
        menu+=("$rate" "$rate fps")
    done
    fps="$(pick "选择帧率" \
        "$device / $format / $resolution 支持多个帧率：" "${menu[@]}")"
fi

width="${resolution%x*}"
height="${resolution#*x}"

# ---------- 3. 命名 ----------
default_name="camera_${device##*/}"
while true; do
    name="$(ask "命名摄像头" \
        "请输入配置名称（只能包含字母、数字、下划线、短横线）：" "$default_name")"
    if [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]]; then
        break
    fi
    ui_msg "名称无效" "名称只能包含字母、数字、下划线、短横线。"
done

# ---------- 3.5 录像配置 ----------
# 录像存储根目录：walle-device/data（walle_home 下的 data）
data_root="$walle_home/data"

# 3.5.1 录像目录名
default_dir_name="recordings"
while true; do
    dir_name="$(ask "录像目录名" \
        "请输入录像目录名（只能包含字母、数字、下划线、短横线）：" "$default_dir_name")"
    if [[ "$dir_name" =~ ^[A-Za-z0-9_-]+$ ]]; then
        break
    fi
    ui_msg "名称无效" "目录名只能包含字母、数字、下划线、短横线。"
done
storage_path="$data_root/$dir_name"

# 3.5.2 存储空间上限
# 先确保 data_root 存在（否则 df 会因路径不存在返回非 0，在 set -e 下让脚本退出）
mkdir -p "$data_root" 2>/dev/null || true
# 取 data_root 所在分区的剩余空间（KB）。|| true 防止 df 异常时脚本退出。
free_kb="$(df -Pk "$data_root" 2>/dev/null | awk 'NR==2{print $4}' || true)"
if [[ -z "$free_kb" || ! "$free_kb" =~ ^[0-9]+$ ]]; then
    # 兜底：仍取不到则用脚本所在分区
    free_kb="$(df -Pk "$script_dir" 2>/dev/null | awk 'NR==2{print $4}' || true)"
fi
if [[ -z "$free_kb" || ! "$free_kb" =~ ^[0-9]+$ ]]; then
    free_kb=0
fi
free_gb_raw=$(( free_kb / 1024 / 1024 ))
free_gb_frac=$(( (free_kb * 10 / 1024 / 1024) % 10 ))
free_gb="${free_gb_raw}.${free_gb_frac}"

record_enabled=true
limit_gb=""
if (( free_kb < 6 * 1024 * 1024 )); then
    ui_msg "录像空间不足" \
        "磁盘剩余空间仅 ${free_gb} GB，低于 6 GB 最低要求，不支持录像。"
    record_enabled=false
    limit_gb="3"
else
    # 上限范围：3 GB ~ floor(剩余空间/2)
    max_limit=$(( free_kb / 2 / 1024 / 1024 ))
    (( max_limit < 3 )) && max_limit=3
    default_limit=16
    (( default_limit > max_limit )) && default_limit=$max_limit
    while true; do
        limit_input="$(ask "存储空间上限" \
            "检测到磁盘剩余空间：${free_gb} GB
可设置范围：3 GB ~ ${max_limit} GB

请输入存储空间上限(GB)：" "$default_limit")"
        if [[ "$limit_input" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
            # 用 python3 做浮点比较，避免 bash 浮点麻烦
            valid="$(python3 -c "
v=float('$limit_input')
print('ok' if 3.0 <= v <= $max_limit else 'bad')" 2>/dev/null)"
            if [[ "$valid" == "ok" ]]; then
                limit_gb="$limit_input"
                break
            fi
        fi
        ui_msg "无效输入" "请输入 3 ~ ${max_limit} 之间的数字。"
    done
fi

# 3.5.3 单文件录像时长
seg_menu=(
    "1800" "30 分钟"
    "3600" "1 小时（推荐）"
    "7200" "2 小时"
)
segment_sec="$(pick_notags "单文件录像时长" \
    "请选择每个录像文件的最大时长：" "${seg_menu[@]}")"

# 3.5.4 自动录像（whiptail/dialog/纯文本均默认“是”）
auto_record=true
if ! ui_confirm "自动录像" \
    "Walle 服务启动成功后，是否自动开始录像？

默认：是。选择“否”时，可通过 Web/App 手动开始录像。"; then
    auto_record=false
fi

record_summary="录像功能: 禁用（磁盘不足）"
if [[ "$record_enabled" == "true" ]]; then
    record_summary="录像功能: 启用
存储路径: $storage_path
存储空间上限: ${limit_gb} GB
单文件时长: $((segment_sec / 60)) 分钟
是否自动录像: $([[ "$auto_record" == "true" ]] && echo 是 || echo 否)"
fi

summary="
── 摄像头 ──
设备:   $device
格式:   $format
分辨率: ${width}x${height}
帧率:   ${fps} fps
名称:   $name
── 录像 ──
$record_summary"
if ! ui_confirm "确认写入配置" "$summary" 20; then
    echo "已取消，未写入配置。" >&2
    exit 0
fi

# ---------- 4. 原子写入 JSON（schema_version=1，camera / record 分块） ----------
mkdir -p "$(dirname "$output")"
tmp="$(mktemp "${output}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
python3 - "$tmp" "$name" "$device" "$format" "$width" "$height" "$fps" \
    "$record_enabled" "$dir_name" "$storage_path" "$limit_gb" "$segment_sec" \
    "$auto_record" <<'PY'
import json
import sys

(path, name, device, fmt, width, height, fps,
 enabled, dir_name, storage_path, limit_gb, segment_sec,
 auto_record) = sys.argv[1:14]
fps_value = float(fps)
if fps_value.is_integer():
    fps_value = int(fps_value)
data = {
    "schema_version": 1,
    "camera": {
        "name": name,
        "device": device,
        "format": fmt,
        "width": int(width),
        "height": int(height),
        "framerate": fps_value,
    },
    "record": {
        "enabled": enabled == "true",
        "dir_name": dir_name,
        "storage_path": storage_path,
        "storage_limit_gb": float(limit_gb),
        "segment_duration_sec": int(segment_sec),
        "auto_record": auto_record == "true",
    },
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
chmod 644 "$tmp"
mv "$tmp" "$output"
trap - EXIT

echo
echo "已生成摄像头配置：$output"
cat "$output"
echo
echo "重启后生效："
echo "  开发：cd $script_dir && ./start.sh"
echo "  生产：sudo systemctl restart walle-ai"
if [[ -f /etc/walle-ai/walle_camera.conf ]]; then
    echo
    echo "注意：/etc/walle-ai/walle_camera.conf 已存在，运行时优先使用它。"
    echo "      如生产环境使用该文件，请同步："
    echo "      sudo install -m 644 -o root -g root \"$output\" /etc/walle-ai/walle_camera.conf"
fi
echo "提示：/dev/videoN 编号可能随插口变化，换口后请重新运行本脚本。"
