#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录像点播：ffmpeg 把 fmp4 转封装为 FLV 推到 SRS，App 拉流播放。"""

import json
import logging
import os
import re
import subprocess
import threading
import uuid as _uuid

from . import config

# ---- 录像配置：取自 walle_camera.conf 的 record 块 ----
CAMERA_CONF_NAME = "walle_camera.conf"


def _camera_conf_candidates():
    """walle_camera.conf 查找顺序：WALLE_CAMERA_CONF > /etc/walle-ai > $TURBOPI_HOME/scripts/etc。"""
    explicit = os.environ.get("WALLE_CAMERA_CONF", "").strip()
    if explicit:
        return [os.path.expanduser(explicit)]
    paths = [os.path.join("/etc", "walle-ai", CAMERA_CONF_NAME)]
    home = (os.environ.get("TURBOPI_HOME") or os.environ.get("WALLE_HOME") or "").strip()
    if home:
        paths.append(os.path.join(home, "scripts", "etc", CAMERA_CONF_NAME))
    return paths


def _load_record_enabled():
    """读取 record.enabled；文件缺失或解析失败时按启用处理。"""
    for path in _camera_conf_candidates():
        try:
            with open(path, encoding="utf-8") as f:
                conf = json.load(f)
        except (OSError, ValueError):
            continue
        return bool((conf.get("record") or {}).get("enabled", True))
    return True


def _load_record_conf():
    """读取 walle_camera.conf 的 record 块，返回 dict；缺失时返回空 dict。"""
    for path in _camera_conf_candidates():
        try:
            with open(path, encoding="utf-8") as f:
                conf = json.load(f)
        except (OSError, ValueError):
            continue
        return conf.get("record") or {}
    return {}


RECORD_ENABLED = _load_record_enabled()
RECORD_STORAGE_PATH = _load_record_conf().get("storage_path", "")

# 文件名白名单：rec_YYYYMMDD_HHMMSS[.n].fmp4（与 record_video 节点一致）
_FILENAME_RE = re.compile(r"^rec_\d{8}_\d{6}(_\d+)?\.fmp4$")


class RecordPlayer:
    """录像点播播放器。

    同一时刻只允许一路播放（新请求会先停掉旧的）。H.264 视频 + AAC
    音频均用 -c copy 直接转封装，不重编码，CPU 占用极低。
    支持 offset_sec 从指定时间点开始推流（-ss 放 -i 前，快速跳关键帧）。
    """

    def __init__(self, logger=None):
        self._proc = None
        self._stream = None
        self._filename = None
        self._lock = threading.Lock()
        self._log = logger or logging.getLogger("RecordPlayer")

    def is_playing(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def stream(self):
        return self._stream

    def _stream_name(self):
        short = (config.DEVICE_ID or "walle")[:8]
        return "record_%s_%s" % (short, _uuid.uuid4().hex[:8])

    def _rtmp_url(self, stream):
        return "rtmp://%s:1935/live/%s?secret=%s" % (
            config.MEDIA_SERVER_HOST, stream, config.MEDIA_SERVER_SECRET)

    def _flv_url(self, stream):
        return "http://%s:%d/live/%s.flv" % (
            config.MEDIA_SERVER_HOST, config.RECORD_PLAY_FLV_PORT, stream)

    def _whep_url(self, stream):
        return "https://%s/rtc/v1/whep/?app=live&stream=%s" % (
            config.MEDIA_SERVER_HOST, stream)

    def play(self, filename, offset_sec=0):
        """按文件名推流，返回 (ok, cause, urls)。urls = {stream, flv_url, whep_url}"""
        if not config.MEDIA_SERVER_HOST or not config.MEDIA_SERVER_SECRET:
            return False, "未配置媒体服务器（MEDIA_SERVER_HOST/SECRET）", None
        if not RECORD_STORAGE_PATH:
            return False, "未配置录像存储路径（walle_camera.conf record.storage_path）", None
        if not _FILENAME_RE.match(filename):
            return False, "文件名不合法", None
        filepath = os.path.join(RECORD_STORAGE_PATH, filename)
        real = os.path.realpath(filepath)
        if not real.startswith(os.path.realpath(RECORD_STORAGE_PATH) + os.sep):
            return False, "非法路径", None
        if not os.path.isfile(real):
            return False, "录像文件不存在", None

        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()

            stream = self._stream_name()
            cmd = [
                config.RECORD_PLAY_FFMPEG_BIN,
                "-hide_banner", "-loglevel", "error", "-y",
            ]
            if offset_sec > 0:
                # -ss 放 -i 前：快速跳到最近关键帧
                cmd += ["-ss", str(offset_sec)]
            cmd += [
                "-re", "-i", real,
                "-c", "copy",
                "-f", "flv", self._rtmp_url(stream),
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                self._proc = None
                return False, "启动 ffmpeg 失败：%s" % exc, None
            self._stream = stream
            self._filename = filename
            # rclpy 的 logger 只接受一个已格式化好的 message（不支持 % 参数），
            # 传多余的位置参数会抛 TypeError —— 之前它把点播应答整个吞掉了。
            self._log.info("录像点播开始：%s -> %s（offset=%ss）"
                           % (filename, stream, offset_sec))

        return True, "ok", {
            "stream": stream,
            "flv_url": self._flv_url(stream),
            "whep_url": self._whep_url(stream),
        }

    def stop(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
                self._log.info("录像点播已停止：%s" % self._stream)
            self._proc = None
            self._stream = None
            self._filename = None
