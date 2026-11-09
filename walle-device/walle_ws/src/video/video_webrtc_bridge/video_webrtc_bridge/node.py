#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video_webrtc_bridge

订阅 /image_raw，收到开始推流指令后把 RGB 帧通过管道送给 ffmpeg，编码为 H.264
并推 RTMP 到 Oryx/SRS；App 继续用 WHEP 拉流。停止指令或 MQTT 网关心跳丢失时自动停流。

对外接口：
  服务  /video_webrtc_bridge/stream_control   interfaces/srv/VideoStreamControl
  状态  /video_webrtc_bridge/status           interfaces/msg/VideoStreamStatus (transient_local)
"""

import os
import re
import subprocess
import tempfile
import threading
import time

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from std_msgs.msg import Bool

from interfaces.msg import VideoStreamStatus
from interfaces.srv import VideoStreamControl


def _load_env_file(path):
    """读取 KEY=VALUE 环境文件（不覆盖已存在的变量），用于拿 WHIP_SECRET。"""
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class _LogSink:
    """ffmpeg 实时输出落盘：独立线程写文件、超过上限滚动、内存保留尾部用于报错。"""

    def __init__(self, path, header=b"", max_bytes=20 * 1024 * 1024, keep_tail=8192):
        self.path = path
        self.header = header or b""
        self.max_bytes = max_bytes
        self.keep_tail = keep_tail
        self.fallback_error = None
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.size = 0
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
            if self.fh is None:
                return
            try:
                if self.max_bytes and self.size >= self.max_bytes:
                    self._rotate()
                if self.fh is None:
                    return
                self.fh.write(data)
                self.fh.flush()
                self.size += len(data)
            except Exception as exc:  # noqa: BLE001
                self.fallback_error = str(exc)

    def _rotate(self):
        try:
            self.fh.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.replace(self.path, self.path + ".1")
        except Exception as exc:  # noqa: BLE001
            self.fallback_error = f"日志滚动失败: {exc}"
        try:
            self.fh = None
            self._open()
            note = (f"===== {_now_str()} 日志超过 {self.max_bytes} 字节，"
                    f"已滚动为 {os.path.basename(self.path)}.1，继续记录 =====\n")
            self.fh.write(note.encode("utf-8"))
            self.fh.flush()
            self.size = self.fh.tell()
        except Exception as exc:  # noqa: BLE001
            self.fallback_error = f"日志滚动后重开失败: {exc}"

    def tail(self, limit=400):
        return bytes(self.buf).decode("utf-8", "replace").strip()[-limit:]

    _ERROR_HINTS = ("error", "cannot", "invalid", "no such", "failed", "failure",
                    "not found", "denied", "busy", "refused", "unable", "unsupported")

    def error_line(self, limit=400):
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
            except Exception:  # noqa: BLE001
                pass
            self.fh = None


class VideoWebrtcBridge(Node):
    def __init__(self):
        super().__init__("video_webrtc_bridge")

        # 先读环境文件，再声明依赖环境变量的参数
        env_file = self.declare_parameter("env_file", "/etc/walle-ai.conf").value
        _load_env_file(env_file)

        self.image_topic = self.declare_parameter("image_topic", "/image_raw").value
        self.camera_device = self.declare_parameter("camera_device", "/dev/video0").value
        self.framerate = float(self.declare_parameter("framerate", 30.0).value)
        self.room_pattern = self.declare_parameter(
            "room_number_pattern", r"^[A-Za-z0-9_-]{1,64}$").value

        default_rtmp = os.environ.get(
            "VIDEO_RTMP_URL_TEMPLATE",
            "rtmp://www.baidong.xyz:1935/live/{room}?secret={secret}")
        self.rtmp_url_template = self.declare_parameter(
            "rtmp_url_template", default_rtmp).value
        # 安全：whip_secret 只从环境文件/环境变量读取，不声明为 ROS 参数，
        # 避免 `ros2 param get/dump` 泄露明文（P0-2.3）
        self.whip_secret = os.environ.get("WHIP_SECRET", "").strip()
        self.video_bitrate = self.declare_parameter("video_bitrate", "1000k").value
        self.video_gop = int(self.declare_parameter("video_gop", 60).value)
        self.video_preset = self.declare_parameter("video_preset", "ultrafast").value

        self.audio_enable = bool(self.declare_parameter("audio_enable", True).value)
        self.audio_device = self.declare_parameter("audio_device", "default").value
        self.audio_codec = self.declare_parameter("audio_codec", "aac").value
        self.audio_bitrate = self.declare_parameter("audio_bitrate", "128k").value
        self.audio_fragment_size = int(
            self.declare_parameter("audio_fragment_size", 1024).value)
        self.audio_probe_timeout = float(
            self.declare_parameter("audio_probe_timeout", 3.0).value)

        self.startup_grace = float(self.declare_parameter("startup_grace", 3.0).value)
        self.first_frame_timeout = float(
            self.declare_parameter("first_frame_timeout", 3.0).value)
        self.log_dir = self.declare_parameter(
            "log_dir", os.path.expanduser("~/.ros/log/video_webrtc_bridge")).value
        self.log_max_bytes = int(
            self.declare_parameter("log_max_bytes", 20 * 1024 * 1024).value)

        self.heartbeat_topic = self.declare_parameter(
            "heartbeat_topic", "/net_mqtt_bridge/heartbeat").value
        self.heartbeat_timeout = float(
            self.declare_parameter("heartbeat_timeout", 5.0).value)

        # 运行状态
        self._lock = threading.RLock()
        self._bridge = CvBridge()
        self._image_group = ReentrantCallbackGroup()
        self._service_group = MutuallyExclusiveCallbackGroup()
        self._image_sub = None
        self._frame = None
        self._frame_size = None
        self._frame_seq = 0
        self._frame_event = threading.Event()
        self._starting = False
        self._streaming = False
        self._proc = None
        self._sink = None
        self._writer_stop = None
        self._writer_thread = None
        self._watch_thread = None
        self._stream_id = 0
        self._room = ""
        self._device = ""
        self._rtmp_url = ""
        self._command = ""
        self._started_at = ""
        self._started_ts = 0.0
        self._audio = self._empty_audio()
        self._last_heartbeat = time.monotonic()
        self._last_convert_error = 0.0

        status_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._status_pub = self.create_publisher(
            VideoStreamStatus, "~/status", status_qos)
        self.create_subscription(
            Bool, self.heartbeat_topic, self._on_heartbeat, 10)
        self.create_service(
            VideoStreamControl, "~/stream_control", self._on_stream_control,
            callback_group=self._service_group)
        self.create_timer(1.0, self._publish_status)
        self.create_timer(0.5, self._check_heartbeat)

        self.get_logger().info(
            "video_webrtc_bridge 启动：image=%s camera=%s "
            "service=~/stream_control heartbeat=%s"
            % (self.image_topic, self.camera_device, self.heartbeat_topic))

    # ------------------------------------------------------------------ 基础工具
    def _empty_audio(self):
        return {
            "available": False,
            "device": None,
            "reason": None,
            "codec": getattr(self, "audio_codec", "aac"),
            "bitrate": getattr(self, "audio_bitrate", "128k"),
        }

    def _mask_url(self, url):
        return re.sub(r"(secret=)[^&]+", r"\1***", url or "")

    def _rtmp_secret_ready(self):
        return "{secret}" not in self.rtmp_url_template or bool(self.whip_secret)

    def _build_rtmp_url(self, room):
        return self.rtmp_url_template.format(room=room, secret=self.whip_secret)

    def _ensure_image_subscription(self):
        if self._image_sub is not None:
            return
        image_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._image_sub = self.create_subscription(
            Image, self.image_topic, self._on_image, image_qos,
            callback_group=self._image_group)

    # ------------------------------------------------------------------ 图像输入
    def _on_image(self, msg):
        with self._lock:
            if not (self._starting or self._streaming):
                return
        try:
            # usb_cam 的 mjpeg2rgb 输出 rgb8 且 step 连续，直接取字节可省一次转换
            if msg.encoding == "rgb8" and msg.step == msg.width * 3:
                data = bytes(msg.data)
            else:
                data = self._bridge.imgmsg_to_cv2(
                    msg, desired_encoding="rgb8").tobytes()
        except Exception as exc:  # noqa: BLE001
            now = time.monotonic()
            if now - self._last_convert_error > 5.0:
                self._last_convert_error = now
                self.get_logger().warning("图像格式转换失败: %s" % exc)
            return

        with self._lock:
            self._frame = data
            self._frame_size = (msg.width, msg.height)
            self._frame_seq += 1
        self._frame_event.set()

    # ------------------------------------------------------------------ 服务回调
    def _on_stream_control(self, request, response):
        try:
            if request.start:
                return self._handle_start(request, response)
            return self._handle_stop(request, response)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().exception("处理推流请求失败")
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = str(exc)
            return response

    def _fill_audio(self, response, audio):
        response.audio_available = bool(audio.get("available"))
        response.audio_device = audio.get("device") or ""
        response.audio_codec = audio.get("codec") or ""
        response.audio_bitrate = audio.get("bitrate") or ""
        response.audio_reason = audio.get("reason") or ""

    def _handle_start(self, request, response):
        device = (request.video_device or "").strip()
        room = (request.room_number or "").strip()
        response.video_device = device
        response.room_number = room
        # 能收到启动请求说明网关在线，刷新心跳，避免启动宽限期被看门狗误杀
        self._last_heartbeat = time.monotonic()

        if not re.match(r"^/dev/video\d+$", device) or not os.path.exists(device):
            response.result = -1
            response.cause = "没有此摄像头设备"
            response.action = "failed"
            response.error = "设备节点不存在: %s" % (device or "<空>")
            return response

        if not re.match(self.room_pattern, room):
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = "room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）"
            return response

        if not self._rtmp_secret_ready():
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = "未配置 Oryx 推流 secret（请在 /etc/walle-ai.conf 设置 WHIP_SECRET）"
            return response

        if device != self.camera_device:
            self.get_logger().warning(
                "请求的 %s 不是当前图像源 %s，将按 %s 推流"
                % (device, self.camera_device, self.image_topic))

        with self._lock:
            proc = self._proc
            streaming = bool(proc is not None and proc.poll() is None)
            same = streaming and self._room == room and self._device == device
            prev_room = self._room if streaming else ""
            prev_device = self._device if streaming else ""
            if same:
                response.result = 0
                response.cause = "ok"
                response.action = "already_streaming"
                response.pid = int(proc.pid)
                response.rtmp_url = self._rtmp_url
                response.command = self._command
                response.started_at = self._started_at
                response.started_ts = self._started_ts
                self._fill_audio(response, self._audio)
                return response

        if streaming:
            self._stop_stream("收到新的推流请求")

        # 等第一帧，确定分辨率和编码格式
        self._starting = True
        self._frame_event.clear()
        self._ensure_image_subscription()
        got_frame = self._frame_event.wait(timeout=self.first_frame_timeout)
        self._starting = False
        if not got_frame:
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = "等待 %s 图像超时（%.1fs）" % (
                self.image_topic, self.first_frame_timeout)
            return response

        with self._lock:
            if self._frame is None or self._frame_size is None:
                response.result = -2
                response.cause = "命令执行失败"
                response.action = "failed"
                response.error = "没有可用的图像帧"
                return response
            width, height = self._frame_size

        audio = self._detect_microphone()
        url = self._build_rtmp_url(room)
        cmd = self._build_ffmpeg_cmd(width, height, url, audio)
        masked_url = self._mask_url(url)
        command_text = " ".join(cmd).replace(url, masked_url)
        started_at = _now_str()
        started_ts = time.time()
        header = (
            "\n===== %s 启动推流 room=%s device=%s =====\n$ %s\n"
            % (started_at, room, device, command_text)
        ).encode("utf-8", "replace")
        sink = self._make_log_sink(header)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=True,
            )
        except FileNotFoundError:
            sink.close()
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
            return response
        except Exception as exc:  # noqa: BLE001
            sink.close()
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = "启动 ffmpeg 失败: %s" % exc
            return response

        threading.Thread(
            target=self._log_pump, args=(proc, sink),
            daemon=True, name="video-log").start()

        with self._lock:
            self._stream_id += 1
            stream_id = self._stream_id
            self._streaming = True
            self._proc = proc
            self._sink = sink
            self._writer_stop = threading.Event()
            self._room = room
            self._device = device
            self._rtmp_url = masked_url
            self._command = command_text
            self._started_at = started_at
            self._started_ts = started_ts
            self._audio = audio
            writer_stop = self._writer_stop

        writer = threading.Thread(
            target=self._writer_loop,
            args=(proc, writer_stop, stream_id),
            daemon=True, name="video-writer")
        writer.start()
        with self._lock:
            self._writer_thread = writer

        # 宽限期内退出：参数/设备/网络有问题，直接判失败
        try:
            code = proc.wait(timeout=self.startup_grace)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            writer_stop.set()
            writer.join(timeout=1.0)
            time.sleep(0.2)
            tail = sink.error_line()
            self._reset_stream(stream_id)
            sink.close()
            response.result = -2
            response.cause = "命令执行失败"
            response.action = "failed"
            response.error = "ffmpeg 退出码 %s（日志: %s）%s" % (
                code, sink.path, (": " + tail) if tail else "")
            return response

        watch = threading.Thread(
            target=self._watch_loop, args=(proc, sink, stream_id, room),
            daemon=True, name="video-watch")
        watch.start()
        with self._lock:
            self._watch_thread = watch

        response.result = 0
        response.cause = "ok"
        response.action = "restarted" if prev_room else "started"
        response.pid = int(proc.pid)
        response.rtmp_url = masked_url
        response.command = command_text
        response.started_at = started_at
        response.started_ts = started_ts
        response.previous_room_number = prev_room
        response.previous_video_device = prev_device
        self._fill_audio(response, audio)
        self._publish_status()
        self.get_logger().info(
            "开始推流: %s（room=%s, pid=%s, 日志=%s）"
            % (masked_url, room, proc.pid, sink.path))
        return response

    def _handle_stop(self, request, response):
        req_room = (request.room_number or "").strip()
        with self._lock:
            proc = self._proc
            streaming = bool(proc is not None and proc.poll() is None)
            current_room = self._room
            meta = {
                "room_number": self._room,
                "video_device": self._device,
                "pid": int(proc.pid) if proc else 0,
                "started_at": self._started_at,
                "rtmp_url": self._rtmp_url,
                "audio": dict(self._audio),
            }
        if not streaming:
            response.result = -1
            response.cause = "当前没有设备在推流"
            response.action = "failed"
            response.stopped = False
            response.room_number = req_room
            return response

        if req_room and current_room and req_room != current_room:
            response.result = -1
            response.cause = "当前没有设备在推流"
            response.action = "failed"
            response.stopped = False
            response.room_number = req_room
            response.error = ("当前推流房间号是 %s，与请求的 %s 不一致"
                              % (current_room, req_room))
            return response

        stopped = self._stop_stream("收到停止推流请求")
        response.room_number = meta["room_number"]
        response.video_device = meta["video_device"]
        response.pid = meta["pid"]
        response.rtmp_url = meta["rtmp_url"]
        response.started_at = meta["started_at"]
        self._fill_audio(response, meta["audio"])
        if not stopped:
            response.result = -1
            response.cause = "当前没有设备在推流"
            response.action = "failed"
            response.stopped = False
            return response
        response.result = 0
        response.cause = "ok"
        response.action = "stopped"
        response.stopped = True
        return response

    # ------------------------------------------------------------------ 推流进程
    def _build_ffmpeg_cmd(self, width, height, url, audio):
        fps = int(round(self.framerate))
        cmd = [
            "ffmpeg",
            "-loglevel", "info",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "%dx%d" % (width, height),
            "-r", str(fps),
            "-i", "-",
        ]
        if audio.get("available"):
            cmd += [
                "-thread_queue_size", "8192",
                "-f", "pulse",
                "-fragment_size", str(self.audio_fragment_size),
                "-i", audio.get("device") or self.audio_device,
            ]
        cmd += [
            "-c:v", "libx264",
            "-preset", self.video_preset,
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", str(self.video_gop),
            "-b:v", self.video_bitrate,
        ]
        if audio.get("available"):
            cmd += [
                "-c:a", self.audio_codec,
                "-b:a", self.audio_bitrate,
                "-vsync", "passthrough",
                "-max_interleave_delta", "0",
                "-muxdelay", "0.1",
                "-muxpreload", "0",
                "-rtmp_live", "live",
            ]
        else:
            cmd += ["-an"]
        cmd += ["-f", "flv", url]
        return cmd

    def _writer_loop(self, proc, stop_event, stream_id):
        """按固定帧率把最新帧写进 ffmpeg stdin，保证视频时间轴与音频一致。"""
        period = 1.0 / max(self.framerate, 1.0)
        next_t = time.monotonic()
        last_seq = -1
        frame = None
        while not stop_event.is_set():
            with self._lock:
                if self._stream_id != stream_id:
                    break
                if self._frame_seq != last_seq:
                    frame = self._frame
                    last_seq = self._frame_seq
            if frame is None:
                stop_event.wait(0.01)
                continue
            try:
                view = memoryview(frame)
                while view:
                    written = proc.stdin.write(view)
                    if not written:
                        break
                    view = view[written:]
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                break
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                stop_event.wait(delay)
            else:
                next_t = time.monotonic()

    def _log_pump(self, proc, sink):
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

    def _watch_loop(self, proc, sink, stream_id, room):
        code = proc.wait()
        tail = sink.tail()
        meta = self._reset_stream(stream_id)
        if meta is None:
            return
        if getattr(proc, "_intentional_stop", False):
            self.get_logger().info("推流已按要求停止（room=%s, exit=%s）" % (room, code))
        elif code == 0:
            self.get_logger().info("推流进程已退出（room=%s, exit=0）" % room)
        else:
            self.get_logger().warning(
                "推流进程异常退出（room=%s, exit=%s）%s"
                % (room, code, ("，stderr: " + tail) if tail else ""))
        self._publish_status()

    def _reset_stream(self, stream_id):
        with self._lock:
            if stream_id != self._stream_id:
                return None
            meta = {
                "room_number": self._room,
                "video_device": self._device,
                "pid": int(self._proc.pid) if self._proc else 0,
                "started_at": self._started_at,
                "rtmp_url": self._rtmp_url,
                "audio": dict(self._audio),
            }
            self._stream_id += 1
            self._streaming = False
            self._proc = None
            self._sink = None
            self._writer_stop = None
            self._room = ""
            self._device = ""
            self._rtmp_url = ""
            self._command = ""
            self._started_at = ""
            self._started_ts = 0.0
            self._audio = self._empty_audio()
            return meta

    def _stop_stream(self, reason=""):
        with self._lock:
            proc = self._proc
            stream_id = self._stream_id
            writer_stop = self._writer_stop
            writer = self._writer_thread
            watch = self._watch_thread
            meta = {
                "room_number": self._room,
                "video_device": self._device,
                "pid": int(proc.pid) if proc else 0,
            }
        if proc is None or proc.poll() is not None:
            self._reset_stream(stream_id)
            return False
        proc._intentional_stop = True
        if writer_stop is not None:
            writer_stop.set()
        # 先关 stdin 让 ffmpeg 读到 EOF 并自行收尾；只发 SIGTERM 时它可能
        # 阻塞在 read(stdin) 上，3 秒后只能被 SIGKILL。
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            self.get_logger().exception("停止推流进程失败")
        if writer is not None and writer.is_alive():
            writer.join(timeout=1.0)
        # 让看护线程先完成清理和日志（它负责打印“推流已按要求停止”）
        if watch is not None and watch.is_alive() and watch is not threading.current_thread():
            watch.join(timeout=1.5)
        self._reset_stream(stream_id)
        self._publish_status()
        self.get_logger().info(
            "已停止推流%s（room=%s, pid=%s）"
            % (("：" + reason) if reason else "",
               meta["room_number"], meta["pid"]))
        return True

    # ------------------------------------------------------------------ 状态/心跳
    def _publish_status(self):
        msg = VideoStreamStatus()
        with self._lock:
            msg.streaming = bool(self._streaming and self._proc is not None)
            msg.room_number = self._room
            msg.video_device = self._device
            msg.rtmp_url = self._rtmp_url
            msg.pid = int(self._proc.pid) if self._proc else 0
            msg.started_at = self._started_at
            msg.started_ts = float(self._started_ts)
            msg.audio_available = bool(self._audio.get("available"))
            msg.audio_device = self._audio.get("device") or ""
            msg.audio_codec = self._audio.get("codec") or ""
            msg.audio_bitrate = self._audio.get("bitrate") or ""
            msg.audio_reason = self._audio.get("reason") or ""
        try:
            self._status_pub.publish(msg)
        except Exception:  # noqa: BLE001
            pass

    def _on_heartbeat(self, msg):
        if msg.data:
            self._last_heartbeat = time.monotonic()

    def _check_heartbeat(self):
        with self._lock:
            streaming = self._streaming
            starting = self._starting
        if not streaming or starting:
            return
        if time.monotonic() - self._last_heartbeat > self.heartbeat_timeout:
            self.get_logger().warning("MQTT 网关心跳丢失，自动停止推流")
            self._stop_stream("MQTT 网关心跳丢失")

    # ------------------------------------------------------------------ 音频探测
    def _pactl_list_names(self, kind):
        try:
            proc = subprocess.run(
                ["pactl", "list", "short", kind],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.audio_probe_timeout,
            )
        except FileNotFoundError:
            return None, "未安装 pactl（sudo apt install -y pulseaudio-utils）"
        except subprocess.TimeoutExpired:
            return None, "pactl 探测超时（>%.1fs）" % self.audio_probe_timeout
        except Exception as exc:  # noqa: BLE001
            return None, "调用 pactl 失败: %s" % exc

        if proc.returncode != 0:
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
            return None, "pactl 返回 %s（PulseAudio 未运行？）%s" % (
                proc.returncode, detail)
        names = []
        for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
            cols = line.split("\t")
            if len(cols) > 1 and cols[1].strip():
                names.append(cols[1].strip())
        return names, None

    def _pulse_has_input_source(self):
        names, reason = self._pactl_list_names("sources")
        if names is None:
            return None, reason
        if any(not n.endswith(".monitor") for n in names):
            return True, None
        return False, "未检测到录音源（没有麦克风，或只有 *.monitor 回环源）"

    def _ffmpeg_audio_probe(self):
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "pulse", "-i", self.audio_device,
                 "-t", "0.2", "-f", "null", "-"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.audio_probe_timeout + 3,
            )
        except FileNotFoundError:
            return False, "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
        except subprocess.TimeoutExpired:
            return False, "ffmpeg 音频探测超时"
        except Exception as exc:  # noqa: BLE001
            return False, "ffmpeg 音频探测失败: %s" % exc
        if proc.returncode == 0:
            return True, None
        tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        return False, "ffmpeg 无法读取默认录音源" + (
            (": " + tail[0][:200]) if tail else "")

    def _detect_microphone(self):
        info = self._empty_audio()
        if not self.audio_enable:
            info["reason"] = "音频采集已关闭（audio_enable=false）"
            return info
        state, reason = self._pulse_has_input_source()
        if state is None:
            ok, ff_reason = self._ffmpeg_audio_probe()
            if not ok:
                info["reason"] = "%s；%s" % (reason, ff_reason)
                return info
        elif state is False:
            info["reason"] = reason
            return info
        info["available"] = True
        info["device"] = self.audio_device
        return info

    # ------------------------------------------------------------------ 日志
    def _make_log_sink(self, header):
        base_dir = self.log_dir or os.path.expanduser("~/.ros/log/video_webrtc_bridge")
        path = os.path.join(
            base_dir, "ffmpeg_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
        try:
            return _LogSink(path, header, max_bytes=self.log_max_bytes)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning(
                "无法写入 ffmpeg 日志 %s（%s），改用临时文件" % (path, exc))
            fd, tmp = tempfile.mkstemp(prefix="ffmpeg_", suffix=".log")
            os.close(fd)
            sink = _LogSink(tmp, header, max_bytes=self.log_max_bytes)
            sink.fallback_error = str(exc)
            return sink

    def shutdown(self):
        self._stop_stream("节点退出")


def main(args=None):
    rclpy.init(args=args)
    node = None
    executor = None
    try:
        node = VideoWebrtcBridge()
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.shutdown()
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
