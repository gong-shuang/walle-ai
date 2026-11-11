#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摄像头能力探测与推流前 preflight（v4l2-ctl）。"""
import logging
import os
import re
import subprocess

from ld_config import *

# ==================== 摄像头能力探测（v4l2-ctl） ====================

def _v4l2(args, timeout=CAM_CMD_TIMEOUT):
    """执行一条 v4l2-ctl 命令，返回 CompletedProcess；超时返回 None"""
    return subprocess.run(["v4l2-ctl"] + args, capture_output=True,
                          text=True, timeout=timeout)


def _parse_v4l2_devices(out):
    """
    解析 `v4l2-ctl --list-devices` 输出，得到 {节点: {"name":卡片名, "detail":驱动+总线}}。
    形如：
        USB 2.0 Camera: USB Camera (usb-xhci-hcd.1-1):
            /dev/video0
            /dev/video1
    卡片名取第一个冒号前的部分（即摄像头友好名），冒号后为驱动与总线信息。
    """
    names = {}
    current = None
    for line in out.splitlines():
        if not line.strip():
            current = None
            continue
        if not line[0].isspace():
            head = line.strip().rstrip(":").strip()
            card, _, detail = head.partition(":")
            current = {"name": card.strip() or head, "detail": detail.strip() or None}
        else:
            for node in re.findall(r"/dev/video\d+", line):
                if current:
                    names[node] = current
    return names


def _parse_formats(stdout):
    """解析 `--list-formats-ext` 输出，返回 [{"code","desc","resolutions":[...]}]"""
    formats = []
    curr_fmt = None
    curr_res = None
    for line in stdout.splitlines():
        line = line.strip()
        fmt_match = re.search(r"\[\d+\]: '(\w+)' \((.*?)\)", line)
        if fmt_match:
            curr_fmt = {"code": fmt_match.group(1),
                        "desc": fmt_match.group(2),
                        "resolutions": []}
            formats.append(curr_fmt)
            curr_res = None
            continue
        res_match = re.search(r"Size: Discrete (\d+)x(\d+)", line)
        if res_match and curr_fmt is not None:
            w, h = int(res_match.group(1)), int(res_match.group(2))
            curr_res = {"res": f"{w}x{h}", "width": w, "height": h, "fps": []}
            curr_fmt["resolutions"].append(curr_res)
            continue
        fps_match = re.search(r"Interval: Discrete [\d\.]+s \((\d+\.?\d*) fps\)", line)
        if fps_match and curr_res is not None:
            fps = float(fps_match.group(1))
            if fps not in curr_res["fps"]:
                curr_res["fps"].append(fps)
    # 帧率排序，方便下游直接展示
    for f in formats:
        for r in f["resolutions"]:
            r["fps"].sort()
    return formats


def _video_sysfs_device(device):
    """返回 /sys/class/video4linux/videoN/device，用于判断是否为 USB 采集节点。"""
    node = os.path.basename(os.path.realpath(device))
    return Path("/sys/class/video4linux") / node / "device"


def camera_preflight(device, size=None):
    """推流前校验摄像头节点（USB 采集节点）；size 非空时同时校验分辨率。

    返回 (ok, reason)；size=None 时只校验节点，分辨率由 pick_video_size() 选择。
    """
    real = os.path.realpath(device)
    if CAMERA_DEVICE and os.path.realpath(CAMERA_DEVICE) != real:
        return False, f"只允许使用 CAMERA_DEVICE={CAMERA_DEVICE}"
    if not re.fullmatch(r"/dev/video\d+", real):
        return False, f"不是 /dev/videoN 设备: {device}"
    try:
        sysfs = str(_video_sysfs_device(real).resolve())
    except OSError:
        return False, "无法解析摄像头 sysfs 路径"
    if "usb" not in sysfs.lower():
        return False, "不是 USB 摄像头节点（可能是 ISP/解码/元数据节点）"
    if size is None:
        return True, None
    sizes, error = camera_sizes(real)
    if error:
        return False, error
    if size not in sizes:
        return False, f"摄像头不支持 {size}（可用: {', '.join(sizes) or '未知'}）"
    return True, None


def camera_sizes(device):
    """返回 (["640x480", ...], error)；按 v4l2-ctl 输出顺序去重。"""
    real = os.path.realpath(device)
    try:
        proc = _v4l2(["-d", real, "--list-formats-ext"])
    except FileNotFoundError:
        return [], "未安装 v4l2-ctl（sudo apt install -y v4l-utils）"
    except subprocess.TimeoutExpired:
        return [], "v4l2-ctl 探测超时"
    except Exception as exc:  # noqa: BLE001
        return [], f"v4l2-ctl 探测失败: {exc}"
    if proc.returncode != 0:
        return [], f"v4l2-ctl 返回 {proc.returncode}"
    sizes = []
    for width, height in re.findall(r"Size: Discrete (\d+)x(\d+)", proc.stdout or ""):
        key = f"{width}x{height}"
        if key not in sizes:
            sizes.append(key)
    return sizes, None


def pick_video_size(device, preferred=None):
    """选择推流分辨率：优先 preferred，其次不超过它的最大可用值，最后取最小。

    返回 (size, note)：size 为 None 表示探测失败；note 非空表示发生了降级/回退。
    """
    preferred = preferred or VIDEO_VIDEO_SIZE
    sizes, error = camera_sizes(device)
    if error:
        return None, error
    if not sizes:
        return None, "摄像头未报告任何可用分辨率"
    if preferred in sizes:
        return preferred, None

    def area(res):
        width, height = res.split("x")
        return int(width) * int(height)

    usable = sorted(sizes, key=area)
    preferred_area = area(preferred)
    candidates = [s for s in usable if area(s) <= preferred_area]
    chosen = candidates[-1] if candidates else usable[0]
    return chosen, (f"摄像头不支持 {preferred}，已自动改用 {chosen}"
                    f"（可用: {', '.join(sizes)}）")


def list_cameras():
    """
    枚举当前设备支持的摄像头视频采集能力。

    判定规则（与已验证的脚本一致）：v4l2-ctl --list-devices 拿到所有 /dev/videoN，
    逐个 --list-formats-ext，returncode==0 且输出含 'Size: Discrete' 才算有效摄像头
    节点（过滤掉纯元数据节点）。

    返回 (cameras, error)：
      cameras: [{"node","name","formats":[{code,desc,resolutions[{res,width,height,fps}]}],
                 "format_count","max_resolution","resolution_count"}]
      error:   None 或 描述字符串（如 v4l2-ctl 未安装）
    """
    try:
        proc = _v4l2(["--list-devices"])
    except FileNotFoundError:
        return [], "未安装 v4l2-ctl，请执行: sudo apt install -y v4l-utils"
    except subprocess.TimeoutExpired:
        return [], "v4l2-ctl --list-devices 执行超时"
    except Exception as e:  # noqa: BLE001
        return [], f"调用 v4l2-ctl 失败: {e}"

    if proc.returncode != 0:
        return [], f"v4l2-ctl --list-devices 返回 {proc.returncode}: {(proc.stderr or '').strip()[:200]}"

    names = _parse_v4l2_devices(proc.stdout or "")
    nodes = list(dict.fromkeys(re.findall(r"/dev/video\d+", proc.stdout or "")))

    cameras = []
    for node in nodes:
        try:
            res = _v4l2(["-d", node, "--list-formats-ext"])
        except subprocess.TimeoutExpired:
            logging.warning("探测 %s 格式超时，跳过", node)
            continue
        except Exception as e:  # noqa: BLE001
            logging.warning("探测 %s 格式失败: %s", node, e)
            continue
        # 只有能列出离散分辨率、且命令成功的节点才是真正的视频采集节点
        if res.returncode != 0 or "Size: Discrete" not in (res.stdout or ""):
            continue

        formats = _parse_formats(res.stdout)
        resolutions = [r["res"] for f in formats for r in f["resolutions"]]
        max_res = ""
        if resolutions:
            max_res = max(
                resolutions,
                key=lambda x: (int(x.split("x")[0]) * int(x.split("x")[1])),
            )
        cameras.append({
            "node": node,
            "name": (names.get(node) or {}).get("name"),
            "detail": (names.get(node) or {}).get("detail"),
            "format_count": len(formats),
            "resolution_count": len(resolutions),
            "max_resolution": max_res or None,
            "formats": formats,
        })

    return cameras, None
