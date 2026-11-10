#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音频能力探测（pactl，pactl 缺失时用 ffmpeg 兜底）。"""

import re
import subprocess

from . import config

# ==================== 视频推流（ffmpeg -> RTMP） ====================


def _pactl_list_names(kind):
    """
    `pactl list short sources|sinks` 的名称列。

    返回 (names, reason)：成功时 names 为 [name, ...]（可能为空列表），
    pactl 未安装/PulseAudio 未运行/超时等失败时 names=None、reason 为原因。
    """
    try:
        proc = subprocess.run(
            [config.VIDEO_PACTL_BIN, "list", "short", kind],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=config.VIDEO_AUDIO_PROBE_TIMEOUT,
        )
    except FileNotFoundError:
        return None, "未安装 pactl（sudo apt install -y pulseaudio-utils）"
    except subprocess.TimeoutExpired:
        return None, f"pactl 探测超时（>{config.VIDEO_AUDIO_PROBE_TIMEOUT}s）"
    except Exception as e:  # noqa: BLE001
        return None, f"调用 pactl 失败: {e}"

    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
        return None, f"pactl 返回 {proc.returncode}（PulseAudio 未运行？）{detail}"

    names = []
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        # 输出形如：0<TAB>alsa_input.usb-xxx<TAB>module-alsa-card.c<TAB>s16le ...
        cols = line.split("\t")
        if len(cols) > 1 and cols[1].strip():
            names.append(cols[1].strip())
    return names, None


def _pactl_value(args):
    """执行一条 pactl 查询命令（get-default-sink 等），成功返回 stdout 文本，失败返回 None"""
    try:
        proc = subprocess.run(
            [config.VIDEO_PACTL_BIN] + args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=config.VIDEO_AUDIO_PROBE_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        return (proc.stdout or b"").decode("utf-8", "replace").strip() or None
    except Exception:  # noqa: BLE001
        return None


def _pulse_has_input_source():
    """
    用 pactl 判断 PulseAudio 上是否存在真实录音源（排除 *.monitor 回环源）。
    （*.monitor 是输出设备把播放内容回环出来的虚拟源，不是麦克风。）

    返回 (state, reason)：
      state=True  有录音源
      state=False 没有录音源
      state=None  无法判断（pactl 未安装 / PulseAudio 未运行等），reason 为原因
    """
    names, reason = _pactl_list_names("sources")
    if names is None:
        return None, reason
    if any(not n.endswith(".monitor") for n in names):
        return True, None
    return False, "未检测到录音源（没有麦克风，或只有 *.monitor 回环源）"


def _ffmpeg_audio_probe(device):
    """pactl 不可用时，用 ffmpeg 试读 0.2 秒，验证 `-f pulse` 录音通路确实可用"""
    try:
        proc = subprocess.run(
            [config.VIDEO_FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
             "-f", "pulse", "-i", device, "-t", "0.2", "-f", "null", "-"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=config.VIDEO_AUDIO_PROBE_TIMEOUT + 3,
        )
    except FileNotFoundError:
        return False, "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 音频探测超时"
    except Exception as e:  # noqa: BLE001
        return False, f"ffmpeg 音频探测失败: {e}"

    if proc.returncode == 0:
        return True, None
    tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
    return False, "ffmpeg 无法读取默认录音源" + (f": {tail[0][:200]}" if tail else "")


def detect_microphone():
    """
    判断本机是否有可用麦克风，决定推流是否带声音。

    判定顺序：
      1) VIDEO_AUDIO_ENABLE=False → 直接纯视频；
      2) pactl 有真实录音源（排除 *.monitor）→ 用默认录音源；
      3) pactl 不可用/未运行（精简系统常见）→ 退化为 ffmpeg 试读一次。

    返回 {"available","device","reason","codec","bitrate"}，
    该结构会原样回填到 video/start/result 的 audio 字段，便于 App/日志排查。
    """
    info = {
        "available": False,
        "device": None,
        "reason": None,
        "codec": config.VIDEO_AUDIO_CODEC,
        "bitrate": config.VIDEO_AUDIO_BITRATE,
    }

    if not config.VIDEO_AUDIO_ENABLE:
        info["reason"] = "音频采集已关闭（VIDEO_AUDIO_ENABLE=False）"
        return info

    state, reason = _pulse_has_input_source()
    if state is None:
        ok, ff_reason = _ffmpeg_audio_probe(config.VIDEO_AUDIO_DEVICE)
        if not ok:
            info["reason"] = f"{reason}；{ff_reason}"
            return info
    elif state is False:
        info["reason"] = reason
        return info

    info["available"] = True
    info["device"] = config.VIDEO_AUDIO_DEVICE
    return info


def probe_audio_capture():
    """
    麦克风（录音）能力，供 capability/result 上报。

    可用性判定复用 detect_microphone（含 VIDEO_AUDIO_ENABLE 开关与 ffmpeg 兜底），
    附带 pactl 列出的真实录音源清单（排除 *.monitor）。
    返回 {"available","device","sources","reason"}，任何一步失败都降级为
    available=False + reason，绝不抛异常。
    """
    base = detect_microphone()
    info = {
        "available": base["available"],
        "device": base["device"],
        "sources": [],
        "reason": base["reason"],
    }
    names, _ = _pactl_list_names("sources")
    if names:
        info["sources"] = [n for n in names if not n.endswith(".monitor")]
    return info


def probe_audio_playback():
    """
    音响/扬声器（播放）能力，供 capability/result 上报。

    返回 {"available","device","volume_percent","muted","sinks","reason"}：
      available:       是否存在音频输出设备
      device:          默认输出 sink 名（pactl get-default-sink，老版本没有该命令则取第一个）
      volume_percent:  默认输出音量（0-150+），未知为 None
      muted:           默认输出是否静音，未知为 None
      sinks:           全部输出 sink 名清单
      reason:          不可用原因（pactl 缺失 / 无输出设备）
    任何一步失败都降级，绝不抛异常；音量/静音拿不到时保留 None 而不是报错。
    """
    info = {
        "available": False,
        "device": None,
        "volume_percent": None,
        "muted": None,
        "sinks": [],
        "reason": None,
    }
    names, reason = _pactl_list_names("sinks")
    if names is None:
        info["reason"] = reason
        return info
    if not names:
        info["reason"] = "未检测到音频输出设备（无扬声器/声卡）"
        return info

    info["available"] = True
    info["sinks"] = names
    info["device"] = _pactl_value(["get-default-sink"]) or names[0]

    volume = _pactl_value(["get-sink-volume", info["device"]])
    if volume:
        m = re.search(r"(\d+)%", volume)
        if m:
            info["volume_percent"] = int(m.group(1))

    mute = _pactl_value(["get-sink-mute", info["device"]])
    if mute:
        lowered = mute.lower()
        info["muted"] = ("yes" in lowered) or ("是" in mute)
    return info
