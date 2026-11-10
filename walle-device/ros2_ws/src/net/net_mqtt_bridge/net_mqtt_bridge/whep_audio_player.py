#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机麦克风上行播放端：WHEP 拉 WebRTC 音频（Opus） → 本机声卡。

相比旧方案（ffmpeg 拉 RTMP + pulse 输出）延迟从秒级降到 ~200ms：
  - WebRTC 走 UDP，没有 TCP 队头阻塞；
  - SRS/Oryx 的 RTC 通道按「最新帧」转发，不会把推流起点的音频整段回放；
  - 输出端用 PortAudio 小块写（blocksize = 20ms），并只保留 ~100ms 的抖动缓冲。

用法（由 main.py 以子进程方式拉起）：
    python3 whep_audio_player.py --url 'https://host/rtc/v1/whep/?app=live&stream=xxx_audio'

退出码：0 正常停止；3 缺依赖；4 首帧超时；5 WHEP 协商失败；6 声卡打开失败。
"""
import argparse
import asyncio
import json
import queue
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

TAG = "[whep-audio]"


def log(msg):
    print(f"{TAG} {msg}", file=sys.stderr, flush=True)


try:
    from aiortc import RTCPeerConnection, RTCSessionDescription
except Exception as e:  # noqa: BLE001
    log(f"缺少依赖 aiortc（pip install aiortc），无法拉流：{e}")
    sys.exit(3)

try:
    import sounddevice as sd
except Exception as e:  # noqa: BLE001
    log(f"缺少依赖 sounddevice（apt install libportaudio2 + pip install sounddevice）：{e}")
    sys.exit(3)


def http_sdp(url, sdp, timeout=10):
    """POST SDP Offer，返回 (body, content_type, status)；兼容纯 SDP 与 JSON 两种应答。"""
    req = urllib.request.Request(
        url, data=sdp.encode("utf-8"),
        headers={"Content-Type": "application/sdp", "Accept": "application/sdp"},
        method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace"), resp.headers.get("Content-Type", ""), resp.status
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace"), e.headers.get("Content-Type", ""), e.code


class Player:
    def __init__(self, url, device, latency, first_frame_timeout):
        self.url = url
        self.device = device or None
        self.latency = latency
        self.first_frame_timeout = first_frame_timeout
        self.stop = threading.Event()   # 用 threading.Event：信号处理器里 set 是安全的
        self.frames = queue.Queue(maxsize=6)   # 最多积 6 × 20ms ≈ 120ms，超了就丢最旧的
        self.writer = None
        self.stream = None
        self.writer_error = None
        self.play_started = False

    # ---------------------------------------------------------------- 声卡输出线程
    def _feed(self, frame):
        data = bytes(frame.planes[0])
        if self.frames.full():
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
        self.frames.put_nowait(data)

    def _writer_loop(self, samplerate, channels, blocksize):
        try:
            self.stream = sd.RawOutputStream(
                samplerate=samplerate, channels=channels, dtype="int16",
                blocksize=blocksize, latency=self.latency, device=self.device)
            self.stream.start()
            log(f"声卡已打开：{samplerate}Hz {channels}ch blocksize={blocksize} latency={self.latency}")
        except Exception as e:  # noqa: BLE001
            log(f"打开声卡失败：{e}（可用 --device 指定输出设备，或先跑 "
                f"python3 -m sounddevice 查看设备列表）")
            self.writer_error = e
            return
        while True:
            item = self.frames.get()
            if item is None:
                break
            try:
                self.stream.write(item)
            except Exception as e:  # noqa: BLE001
                log(f"写声卡失败：{e}")
                break
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 主流程
    async def run(self):
        pc = RTCPeerConnection()
        pc.addTransceiver("audio", direction="recvonly")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        t0 = time.time()
        while pc.iceGatheringState != "complete" and time.time() - t0 < 3:
            await asyncio.sleep(0.05)

        log(f"WHEP 协商中：{self.url}")
        body, ctype, status = await asyncio.to_thread(
            http_sdp, self.url, pc.localDescription.sdp)
        if not (200 <= status < 300):
            log(f"WHEP 失败：HTTP {status} {body[:160]}")
            await pc.close()
            return 5
        if "application/sdp" in ctype:
            answer = RTCSessionDescription(sdp=body, type="answer")
        else:
            data = json.loads(body)
            if data.get("code") != 0:
                log(f"WHEP 失败：{str(data)[:160]}")
                await pc.close()
                return 5
            answer = RTCSessionDescription(sdp=data["sdp"], type="answer")
        await pc.setRemoteDescription(answer)

        receiver = pc.getReceivers()[0]
        track = receiver.track
        try:
            first = await asyncio.wait_for(track.recv(), timeout=self.first_frame_timeout)
        except asyncio.TimeoutError:
            log(f"{self.first_frame_timeout}s 内没有收到音频（手机端 WHIP 是否已推流？）")
            await pc.close()
            return 4

        rate = first.sample_rate
        if "s16" not in str(first.format):
            log(f"不支持的解码格式 {first.format}（期望 s16）")
            await pc.close()
            return 6
        channels = 2 if "stereo" in first.layout.name else 1
        self.writer_error = None
        self.writer = threading.Thread(
            target=self._writer_loop,
            args=(rate, channels, first.samples),
            name="audio-writer", daemon=True)
        self.writer.start()
        self._feed(first)
        self.play_started = True
        log(f"开始播放音频（{rate}Hz {channels}ch）")

        while not self.stop.is_set():
            if self.writer_error is not None:
                log(f"输出线程异常退出：{self.writer_error}")
                await pc.close()
                return 6
            try:
                frame = await asyncio.wait_for(track.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                if pc.connectionState in ("failed", "closed"):
                    log(f"WebRTC 连接已{'失败' if pc.connectionState == 'failed' else '关闭'}")
                    break
                continue
            except Exception as e:  # noqa: BLE001
                log(f"拉流结束：{e}")
                break
            self._feed(frame)

        # 通知写线程退出（队列满时先清空，避免 put 永久阻塞）
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        self.frames.put_nowait(None)
        await pc.close()
        if self.writer:
            self.writer.join(timeout=1.5)
        log("已停止播放")
        return 0


def main():
    ap = argparse.ArgumentParser(description="手机麦上行：WHEP 拉流 + 本机播放")
    ap.add_argument("--url", required=True, help="完整 WHEP 地址（stream={room}_audio）")
    ap.add_argument("--device", default="", help="输出设备名/序号（留空=系统默认）")
    ap.add_argument("--latency", default="low", help="PortAudio latency 档位：low/high 或秒数")
    ap.add_argument("--first-frame-timeout", type=float, default=8.0)
    args = ap.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    player = Player(args.url, args.device, args.latency, args.first_frame_timeout)

    def on_signal(*_):
        player.stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_signal)
        except NotImplementedError:
            signal.signal(sig, on_signal)

    code = loop.run_until_complete(player.run())
    loop.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
