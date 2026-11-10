#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""媒体面工具：ffmpeg 参数、推流/拉流地址、日志落地与脱敏、子进程清理。"""
import base64
import ctypes
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from ld_config import *
from ld_utils import *

def build_ffmpeg_cmd(video_device, room_number, audio=None, rtmp_url=None,
                     video_size=None, framerate=None, encoder=None):
    """
    按收到的请求动态拼 ffmpeg 参数（视频设备、房间号都不写死）。
    用参数列表而不是 shell 字符串，避免命令注入。

    audio: detect_microphone() 的返回值；available=True 时追加 PulseAudio 默认
    录音源（-f pulse -i default）并编码为 AAC，否则保持 -an 纯视频。
    rtmp_url: 已构造好的推流地址；不传则按 room_number 现构造。
    video_size / framerate: 本次推流参数；不传则用配置默认值。
    encoder: 视频编码器；不传则用 libx264。
    """
    size = video_size or VIDEO_VIDEO_SIZE
    fps = framerate or VIDEO_FRAMERATE
    enc = encoder or "libx264"
    cmd = [
        VIDEO_FFMPEG_BIN,
        # 不加 -hide_banner：日志里保留 ffmpeg 版本/编译选项/流信息，便于排查
        "-loglevel", VIDEO_LOG_LEVEL,
        "-f", "v4l2",
        "-thread_queue_size", "8192",
        "-video_size", size,
        "-framerate", str(fps),
        "-i", video_device,
    ]

    has_audio = bool(audio and audio.get("available"))
    if has_audio:
        # 音频输入必须紧跟视频输入之后、编码参数之前，且单独给足队列；
        # fragment_size 限制 PulseAudio 每次攒的采样量，直接决定采集端缓冲时长
        cmd += [
            "-thread_queue_size", "8192",
            "-f", "pulse",
            "-fragment_size", str(VIDEO_AUDIO_FRAGMENT_SIZE),
            "-i", audio.get("device") or VIDEO_AUDIO_DEVICE,
        ]

    cmd += ["-c:v", enc]
    if enc == "libx264":
        cmd += ["-preset", VIDEO_PRESET, "-tune", VIDEO_TUNE]
    cmd += [
        "-pix_fmt", "yuv420p",
        "-g", str(VIDEO_GOP),
        "-b:v", VIDEO_BITRATE,
    ]

    if has_audio:
        cmd += [
            "-c:a", VIDEO_AUDIO_CODEC,
            "-b:a", VIDEO_AUDIO_BITRATE,
        ]
    else:
        cmd += ["-an"]

    # 低延迟封装参数（混音流验证过；纯视频流保持原参数不动）：
    #   -vsync passthrough        时间戳直通，不为凑 CFR 复制/丢弃帧
    #   -max_interleave_delta 0   不缓存交错，音视频随到随发（音画等待是延迟主因）
    #   -muxdelay 0.1 -muxpreload 0  最小化 flv 复用器的起始缓冲
    #   -rtmp_live live           声明直播流，rtmp 发送不攒包
    if has_audio:
        cmd += [
            "-vsync", "passthrough",
            "-max_interleave_delta", "0",
            "-muxdelay", "0.1",
            "-muxpreload", "0",
            "-rtmp_live", "live",
        ]

    cmd += [
        "-f", "flv",
        rtmp_url or build_rtmp_url(room_number),
    ]
    return cmd


def build_rtmp_url(room_number):
    return VIDEO_RTMP_URL_TEMPLATE.format(room=room_number, secret=WHIP_SECRET)


def available_video_encoders():
    """枚举 ffmpeg 支持的 H.264 编码器；探测失败返回 None。"""
    try:
        proc = subprocess.run(
            [VIDEO_FFMPEG_BIN, "-hide_banner", "-encoders"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or b"").decode("utf-8", "replace")
    return {name for name in ("libx264", "h264_v4l2m2m") if name in text}


def pick_video_encoder():
    """按 VIDEO_ENCODER 选择编码器，返回 (encoder, note)；note 非空表示发生了回退。"""
    wanted = VIDEO_ENCODER
    encoders = available_video_encoders()
    if encoders is None:
        return "libx264", "无法枚举 ffmpeg 编码器，回退 libx264"
    if wanted == "auto":
        return ("h264_v4l2m2m" if "h264_v4l2m2m" in encoders else "libx264"), None
    if wanted not in ("libx264", "h264_v4l2m2m"):
        return "libx264", f"未知 VIDEO_ENCODER={wanted}，回退 libx264"
    if wanted not in encoders:
        return "libx264", f"ffmpeg 不支持 {wanted}，回退 libx264"
    return wanted, None


_FFMPEG_STARTUP_ERROR_HINTS = (
    "401", "unauthorized", "connection refused", "error opening output",
    "server error", "broken pipe", "invalid data found", "no such file",
)


def ffmpeg_startup_error(tail):
    """启动宽限期后检查 ffmpeg 输出，命中鉴权/连接类错误时返回原因。"""
    lowered = (tail or "").lower()
    if any(hint in lowered for hint in _FFMPEG_STARTUP_ERROR_HINTS):
        return (tail or "")[-300:]
    return None


def rtmp_url_ready():
    """推流地址模板与 secret 是否就绪；返回 (ok, reason)。"""
    if not VIDEO_RTMP_URL_TEMPLATE:
        return False, "未配置 VIDEO_RTMP_URL_TEMPLATE（推流地址模板）"
    if "{room}" not in VIDEO_RTMP_URL_TEMPLATE:
        return False, "VIDEO_RTMP_URL_TEMPLATE 缺少 {room} 占位符"
    if "{secret}" in VIDEO_RTMP_URL_TEMPLATE and not WHIP_SECRET:
        return False, "未配置 Oryx 推流 secret（请在 /etc/walle-ai.conf 设置 WHIP_SECRET）"
    return True, None


def whep_url_ready():
    """手机音频 WHEP 地址模板与鉴权参数是否就绪；返回 (ok, reason)。"""
    if not PHONE_AUDIO_WHEP_URL_TEMPLATE:
        return False, "未配置 PHONE_AUDIO_WHEP_URL_TEMPLATE（手机音频拉流地址模板）"
    for placeholder in ("{stream}", "{expires}", "{token}"):
        if placeholder not in PHONE_AUDIO_WHEP_URL_TEMPLATE:
            return False, f"PHONE_AUDIO_WHEP_URL_TEMPLATE 缺少 {placeholder} 占位符"
    if not PHONE_AUDIO_WHEP_SECRET:
        return False, "未配置 PHONE_AUDIO_WHEP_SECRET（WHEP 鉴权密钥）"
    if PHONE_AUDIO_WHEP_TOKEN_TTL <= 0:
        return False, "PHONE_AUDIO_WHEP_TOKEN_TTL 必须大于 0"
    return True, None


def whep_token(stream, expires):
    """按 Nginx secure_link_md5 规则生成 token：base64url(md5(expires+path+stream secret))。"""
    raw = f"{expires}{PHONE_AUDIO_WHEP_PATH}{stream} {PHONE_AUDIO_WHEP_SECRET}"
    digest = hashlib.md5(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def video_log_path():
    """解析 ffmpeg 日志文件路径。

    - 绝对路径原样使用；
    - 相对路径以 WALLE_LOG_DIR 为基准；未配置时以 main.py 所在目录为基准，
      因此 systemd 与手动从任意工作目录启动都会写到同一位置。
    """
    name = VIDEO_LOG_FILE.replace("{time}", time.strftime("%Y%m%d_%H%M%S"))
    path = os.path.expanduser(name)
    if not os.path.isabs(path):
        base = os.environ.get("WALLE_LOG_DIR", "").strip()
        base = os.path.expanduser(base) if base else os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, path)
    return os.path.abspath(path)


class _LogSink:
    """
    ffmpeg 实时输出的落地目标：
      - 由独立线程持续把 ffmpeg 的 stdout/stderr 写进日志文件（管道始终有人读，不会塞满阻塞 ffmpeg）
      - 单个文件超过 VIDEO_LOG_MAX_BYTES 时自动滚动为 .1 再续写，避免日志无限变大
      - 内存里保留最后 8KB，用于失败时快速给出原因
    """

    def __init__(self, path, header=b"", max_bytes=VIDEO_LOG_MAX_BYTES, keep_tail=8192):
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


def cleanup_video_logs(log_path):
    """清理旧推流日志：按「保留 N 天 / 最多 M 个文件 / 总量 X MB」删除最旧的文件。

    在新建日志前调用，只处理同目录下 ffmpeg_*.log 与 .log.1；当前新文件尚未创建，
    因此不会删到正在写入的日志。
    """
    log_dir = os.path.dirname(log_path) or "."
    try:
        names = [n for n in os.listdir(log_dir)
                 if n.startswith("ffmpeg_") and (n.endswith(".log") or n.endswith(".log.1"))]
    except OSError:
        return
    files = []
    for name in names:
        path = os.path.join(log_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, path))
    files.sort()                      # 最旧在前
    now = time.time()
    kept = 0
    total = 0
    for mtime, size, path in reversed(files):   # 从新到旧决定保留
        too_old = VIDEO_LOG_KEEP_DAYS > 0 and now - mtime > VIDEO_LOG_KEEP_DAYS * 86400
        too_many = VIDEO_LOG_KEEP_FILES > 0 and kept >= VIDEO_LOG_KEEP_FILES
        too_big = (VIDEO_LOG_MAX_TOTAL_MB > 0
                   and total + size > VIDEO_LOG_MAX_TOTAL_MB * 1024 * 1024)
        if too_old or too_many or too_big:
            try:
                os.remove(path)
                logging.info("已清理旧推流日志: %s", path)
            except OSError as exc:
                logging.warning("清理旧推流日志失败 %s: %s", path, exc)
            continue
        kept += 1
        total += size


def make_log_sink(header=b""):
    """按配置创建日志落地点；配置路径不可写时退回临时文件，保证推流本身不受影响"""
    path = video_log_path()
    cleanup_video_logs(path)
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
    """日志/报文里隐藏 secret/token 参数，避免密钥外泄到日志"""
    return re.sub(r"((?:secret|token)=)[^&]+", r"\1***", url)


# 报文脱敏字段（子串匹配，password/phone_audio_whep_secret 等都会命中）
_SENSITIVE_FIELDS = ("password", "secret", "token", "key", "room", "room_number")


def _mask_obj(value):
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            if (any(name in str(key).lower() for name in _SENSITIVE_FIELDS)
                    and not isinstance(item, (dict, list))):
                masked[key] = "***"
            else:
                masked[key] = _mask_obj(item)
        return masked
    if isinstance(value, list):
        return [_mask_obj(item) for item in value]
    if isinstance(value, str):
        return mask_rtmp_url(value)
    return value


def mask_payload(payload, limit=500):
    """日志用报文：password/secret/token/key/room 等字段替换为 ***，URL 参数脱敏后截断。"""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return mask_rtmp_url(payload)[:limit]
    return json.dumps(_mask_obj(data), ensure_ascii=False)[:limit]


# PR_SET_PDEATHSIG：父进程死亡时给子进程发信号（Linux 专用）
_PR_SET_PDEATHSIG = 1
try:
    _LIBC = ctypes.CDLL(None, use_errno=True) if sys.platform.startswith("linux") else None
except OSError:
    _LIBC = None


def _pdeathsig_preexec(parent_pid):
    """返回 Popen 的 preexec_fn：父进程退出/被 kill 时让子进程收到 SIGTERM。

    说明：ffmpeg 与 WHEP 播放器以 start_new_session=True 启动，systemd 的 cgroup
    清理只覆盖服务停止；PDEATHSIG 负责覆盖主进程崩溃/被 kill -9 的场景。
    """
    def _preexec():
        if _LIBC is not None:
            try:
                _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass
        if os.getppid() != parent_pid:
            os._exit(1)
    return _preexec


def _child_preexec():
    """Linux 下返回 PDEATHSIG preexec_fn，其他平台返回 None。"""
    return _pdeathsig_preexec(os.getpid()) if _LIBC is not None else None
