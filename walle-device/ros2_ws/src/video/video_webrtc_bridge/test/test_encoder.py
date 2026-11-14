#!/usr/bin/env python3
"""P2-1.3：H.264 编码器选择与硬件编码探测测试。"""

import pytest

from video_webrtc_bridge import node as video_node
from video_webrtc_bridge.node import (
    HW_H264_ENCODER,
    MPP_H264_ENCODER,
    SOFTWARE_H264_ENCODER,
    VideoWebrtcBridge,
    bitrate_to_bps,
    probe_hw_h264_encoder,
    probe_mpp_h264_encoder,
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


@pytest.mark.parametrize("preference,hw_available,mpp_available,expected", [
    ("auto", True, True, MPP_H264_ENCODER),
    ("auto", False, True, MPP_H264_ENCODER),
    ("auto", True, False, HW_H264_ENCODER),
    ("mpph264enc", True, True, MPP_H264_ENCODER),
    ("mpph264enc", False, False, SOFTWARE_H264_ENCODER),
])
def test_select_encoder_prefers_mpp(preference, hw_available, mpp_available, expected):
    assert select_encoder(preference, hw_available, mpp_available) == expected


@pytest.mark.parametrize("value,default,expected", [
    ("1000k", 1, 1000000),
    ("2M", 1, 2000000),
    ("128k", 1, 128000),
    ("96000", 1, 96000),
    ("", 123, 123),
    ("bad", 456, 456),
])
def test_bitrate_to_bps(value, default, expected):
    assert bitrate_to_bps(value, default) == expected


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


def test_probe_mpp_encoder_ok(monkeypatch):
    monkeypatch.setattr(video_node.subprocess, "run", lambda *a, **k: _Proc(0))
    ok, reason = probe_mpp_h264_encoder()
    assert ok is True
    assert reason is None


def test_probe_mpp_encoder_without_gst_launch(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("gst-launch-1.0")

    monkeypatch.setattr(video_node.subprocess, "run", _raise)
    ok, reason = probe_mpp_h264_encoder()
    assert ok is False
    assert "gst-launch-1.0" in reason


def _bare_node(encoder):
    """只填构建命令需要的属性，不初始化 rclpy 节点。"""
    node = object.__new__(VideoWebrtcBridge)
    node.framerate = 30.0
    node.video_preset = "ultrafast"
    node.video_gop = 60
    node.video_bitrate = "1000k"
    node.camera_device = "/dev/video0"
    node.camera_format = "MJPG"
    node.camera_width = 640
    node.camera_height = 480
    node.ros_image_fps = 10.0
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


def test_build_gst_cmd_video_only():
    cmd = _bare_node(MPP_H264_ENCODER)._build_gst_cmd(
        640, 480, "rtmp://example/live/room", {"available": False})
    assert cmd[:2] == ["gst-launch-1.0", "-q"]
    assert "fdsrc" in cmd and "rawvideoparse" in cmd
    assert "format=rgb" in cmd
    assert "videoconvert" in cmd
    assert "mpph264enc" in cmd
    assert "bps=1000000" in cmd
    assert "rc-mode=cbr" in cmd
    assert "gop=60" in cmd
    assert "flvmux" in cmd and "rtmpsink" in cmd
    assert "location=rtmp://example/live/room" in cmd
    assert "pulsesrc" not in cmd


def test_build_gst_cmd_with_audio():
    cmd = _bare_node(MPP_H264_ENCODER)._build_gst_cmd(
        640, 480, "rtmp://example/live/room",
        {"available": True, "device": "default"})
    assert "pulsesrc" in cmd
    # "default" 是 ffmpeg 写法，pulsesrc 需省略 device 用默认源
    assert "device=default" not in cmd
    assert "voaacenc" in cmd and "bitrate=128000" in cmd
    assert cmd[-1] == "mux."


def test_build_gst_cmd_with_named_audio_device():
    cmd = _bare_node(MPP_H264_ENCODER)._build_gst_cmd(
        640, 480, "rtmp://example/live/room",
        {"available": True, "device": "alsa_input.usb-mic"})
    assert "device=alsa_input.usb-mic" in cmd


def test_build_stream_bin_desc_video_only():
    desc = _bare_node(MPP_H264_ENCODER)._build_stream_bin_desc(
        "rtmp://example/live/room", {"available": False})
    assert "mpph264enc" in desc
    assert "flvmux name=mux" in desc
    assert 'rtmpsink location="rtmp://example/live/room"' in desc
    assert "pulsesrc" not in desc


def test_build_stream_bin_desc_with_default_audio():
    desc = _bare_node(MPP_H264_ENCODER)._build_stream_bin_desc(
        "rtmp://example/live/room", {"available": True, "device": "default"})
    assert "pulsesrc !" in desc
    assert "device=default" not in desc
    assert "voaacenc bitrate=128000" in desc
    assert desc.rstrip().endswith("! mux.")


def test_camera_pipeline_decimates_ros_fps():
    desc = _bare_node(MPP_H264_ENCODER)._camera_pipeline_desc()
    assert "mppjpegdec format=NV12" in desc
    assert "identity drop-probability=0.6667" in desc
    assert "video/x-raw,format=RGB" in desc


def test_camera_pipeline_keeps_full_fps_when_equal():
    node = _bare_node(MPP_H264_ENCODER)
    node.ros_image_fps = 30.0
    assert "drop-probability" not in node._camera_pipeline_desc()


def test_build_h264_cmd_uses_shmsrc():
    node = _bare_node(MPP_H264_ENCODER)
    node.shm_socket = "/tmp/walle-video.sock"
    cmd = node._build_h264_cmd(
        "rtmp://example/live/room", {"available": False})
    assert "shmsrc" in cmd
    assert "socket-path=/tmp/walle-video.sock" in cmd
    assert "h264parse" in cmd
    assert "rtmpsink" in cmd
    assert cmd[-1] == "location=rtmp://example/live/room"
