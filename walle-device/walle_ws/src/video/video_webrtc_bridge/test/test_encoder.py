#!/usr/bin/env python3
"""P2-1.3：H.264 编码器选择与硬件 M2M 探测测试。"""

import pytest

from video_webrtc_bridge import node as video_node
from video_webrtc_bridge.node import (
    HW_H264_ENCODER,
    SOFTWARE_H264_ENCODER,
    VideoWebrtcBridge,
    probe_hw_h264_encoder,
    select_encoder,
)


@pytest.mark.parametrize("preference,hw_available,expected", [
    ("auto", True, HW_H264_ENCODER),
    ("auto", False, SOFTWARE_H264_ENCODER),
    ("", True, HW_H264_ENCODER),
    (None, False, SOFTWARE_H264_ENCODER),
    (SOFTWARE_H264_ENCODER, True, SOFTWARE_H264_ENCODER),
    (HW_H264_ENCODER, True, HW_H264_ENCODER),
    (HW_H264_ENCODER, False, SOFTWARE_H264_ENCODER),
    ("libx264", False, SOFTWARE_H264_ENCODER),
])
def test_select_encoder(preference, hw_available, expected):
    assert select_encoder(preference, hw_available) == expected


class _Proc:
    def __init__(self, returncode, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


def test_probe_hw_encoder_ok(monkeypatch):
    monkeypatch.setattr(video_node.subprocess, "run", lambda *a, **k: _Proc(0))
    ok, reason = probe_hw_h264_encoder()
    assert ok is True
    assert reason is None


def test_probe_hw_encoder_without_device(monkeypatch):
    out = (b"[h264_v4l2m2m @ 0x1] Could not find a valid device\n"
           b"Nothing was written into output file\n")
    monkeypatch.setattr(video_node.subprocess, "run", lambda *a, **k: _Proc(234, out))
    ok, reason = probe_hw_h264_encoder()
    assert ok is False
    assert "Could not find a valid device" in reason


def test_probe_hw_encoder_without_ffmpeg(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(video_node.subprocess, "run", _raise)
    ok, reason = probe_hw_h264_encoder()
    assert ok is False
    assert "ffmpeg" in reason


def _bare_node(encoder):
    """只填 _build_ffmpeg_cmd 需要的属性，不初始化 rclpy 节点。"""
    node = object.__new__(VideoWebrtcBridge)
    node.framerate = 30.0
    node.video_preset = "ultrafast"
    node.video_gop = 60
    node.video_bitrate = "1000k"
    node.audio_fragment_size = 1024
    node.audio_device = "default"
    node.audio_codec = "aac"
    node.audio_bitrate = "128k"
    node._encoder = encoder
    return node


def _cmd_encoder(cmd):
    return cmd[cmd.index("-c:v") + 1]


def test_build_cmd_software_encoder_keeps_preset():
    cmd = _bare_node(SOFTWARE_H264_ENCODER)._build_ffmpeg_cmd(
        640, 480, "rtmp://example/live/room", {"available": False})
    assert _cmd_encoder(cmd) == SOFTWARE_H264_ENCODER
    assert "-preset" in cmd and "ultrafast" in cmd
    assert "-tune" in cmd and "zerolatency" in cmd
    assert cmd[-1] == "rtmp://example/live/room"


def test_build_cmd_hardware_encoder_has_no_preset():
    cmd = _bare_node(HW_H264_ENCODER)._build_ffmpeg_cmd(
        640, 480, "rtmp://example/live/room", {"available": False})
    assert _cmd_encoder(cmd) == HW_H264_ENCODER
    assert "-preset" not in cmd
    assert "-tune" not in cmd
    assert "-b:v" in cmd and "1000k" in cmd
