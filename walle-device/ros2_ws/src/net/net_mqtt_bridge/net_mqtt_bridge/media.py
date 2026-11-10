#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摄像头能力探测、ffmpeg 参数与日志落盘。"""

import logging
import os
import re
import subprocess
import tempfile
import threading
import time

from . import config
from .identity import now_str

# ==================== 摄像头能力探测（v4l2-ctl） ====================


def _v4l2(args, timeout=config.CAM_CMD_TIMEOUT):
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


def rtmp_secret_ready():
    """模板里还留着 {secret} 占位符时，必须配置了 WHIP_SECRET 才能推流。"""
    return "{secret}" not in config.VIDEO_RTMP_URL_TEMPLATE or bool(config.WHIP_SECRET)


def video_log_path():
    """解析 ffmpeg 日志文件路径：支持 {time} 占位符，相对路径按当前工作目录解析"""
    name = config.VIDEO_LOG_FILE.replace("{time}", time.strftime("%Y%m%d_%H%M%S"))
    return os.path.abspath(os.path.expanduser(name))


class _LogSink:
    """
    ffmpeg 实时输出的落地目标：
      - 由独立线程持续把 ffmpeg 的 stdout/stderr 写进日志文件（管道始终有人读，不会塞满阻塞 ffmpeg）
      - 单个文件超过 VIDEO_LOG_MAX_BYTES 时自动滚动为 .1 再续写，避免日志无限变大
      - 内存里保留最后 8KB，用于失败时快速给出原因
    """

    def __init__(self, path, header=b"", max_bytes=config.VIDEO_LOG_MAX_BYTES, keep_tail=8192):
        self.path = path
        self.header = header or b""
        self.max_bytes = max_bytes
        self.keep_tail = keep_tail
        self.fallback_error = None
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.size = 0
        self.written = 0          # 本次推流累计写入字节数（含滚动后的新文件）
        self.rotations = 0
        self.fh = None
        self._open()

    def _open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.fh = open(self.path, "ab")
        if self.header:
            self.fh.write(self.header)
            self.fh.flush()
        self.size = self.fh.tell()

    def write(self, data):
        if not data:
            return
        with self.lock:
            self.buf.extend(data)
            if len(self.buf) > self.keep_tail:
                del self.buf[:len(self.buf) - self.keep_tail]
            self.written += len(data)
            if self.fh is None:
                return
            try:
                # 先判断是否已到上限再写，保证每条日志都完整落在某一个文件里
                if self.max_bytes and self.size >= self.max_bytes:
                    self._rotate()
                if self.fh is None:
                    return
                self.fh.write(data)
                self.fh.flush()
                self.size += len(data)
            except Exception as e:  # noqa: BLE001
                self.fallback_error = str(e)

    def _rotate(self):
        """当前文件超限：改名成 .1，重新开一个新文件继续写（ffmpeg 写的是管道，不受影响）"""
        try:
            self.fh.close()
        except Exception:
            pass
        try:
            os.replace(self.path, self.path + ".1")
        except Exception as e:  # noqa: BLE001
            self.fallback_error = f"日志滚动失败: {e}"
        try:
            self.fh = None
            self._open()
            self.rotations += 1
            note = (f"===== {now_str()} 日志超过 {self.max_bytes} 字节，"
                    f"已滚动为 {os.path.basename(self.path)}.1，继续记录 =====\n")
            self.fh.write(note.encode("utf-8"))
            self.fh.flush()
            self.size = self.fh.tell()
            logging.warning("ffmpeg 日志超过上限，已滚动: %s -> %s.1", self.path, self.path)
        except Exception as e:  # noqa: BLE001
            self.fallback_error = f"日志滚动后重开失败: {e}"

    def tail(self, limit=400):
        return bytes(self.buf).decode("utf-8", "replace").strip()[-limit:]

    _ERROR_HINTS = ("error", "cannot", "invalid", "no such", "failed", "failure",
                    "not found", "denied", "busy", "refused", "unable", "unsupported")

    def error_line(self, limit=400):
        """
        取“最像原因”的那一行：日志级别是 info 时文件开头有整段 banner，
        直接取尾部容易被版本信息淹没，所以优先挑含错误关键词的最后一行。
        """
        text = bytes(self.buf).decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.replace("\r", "\n").splitlines() if ln.strip()]
        picked = [ln for ln in lines
                  if any(k in ln.lower() for k in self._ERROR_HINTS)]
        if picked:
            return picked[-1][:limit]
        return text.strip()[-limit:]

    def close(self):
        with self.lock:
            try:
                if self.fh:
                    self.fh.close()
            except Exception:
                pass
            self.fh = None


def make_log_sink(header=b""):
    """按配置创建日志落地点；配置路径不可写时退回临时文件，保证推流本身不受影响"""
    path = video_log_path()
    try:
        return _LogSink(path, header)
    except Exception as e:  # noqa: BLE001
        logging.warning("无法写入 ffmpeg 日志文件 %s（%s），改用临时文件", path, e)
        fd, tmp = tempfile.mkstemp(prefix="ffmpeg_", suffix=".log")
        os.close(fd)
        sink = _LogSink(tmp, header)
        sink.fallback_error = str(e)
        return sink


def mask_rtmp_url(url):
    """日志/报文里隐藏 secret 参数，避免密钥外泄到日志"""
    return re.sub(r"(secret=)[^&]+", r"\1***", url)


def pump_process_output(proc, sink):
    """把子进程 stdout 持续写入日志落地点（管道始终有人读，避免子进程阻塞）。"""
    try:
        fd = proc.stdout.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            sink.write(chunk)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        sink.close()
