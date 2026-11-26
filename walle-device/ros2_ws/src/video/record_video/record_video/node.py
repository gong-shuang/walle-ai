#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""record_video_node

订阅 /image_raw（sensor_msgs/Image，BGR8），通过 ffmpeg 编码为 H.264，并同时从
麦克风采集音频编码为 AAC，一起封装为 fragmented MP4（fMP4）落盘到本地。支持：
  - 开始 / 停止录像
  - 按配置时长自动分片（每个分片一个独立 fMP4 文件）
  - 存储空间上限管理（FIFO 回收最旧文件）
  - 录像列表查询 / 删除
  - HTTP 静态服务供 Web/App 播放 fMP4

对外接口：
  服务  /record_video/control   interfaces/srv/RecordControl  (start/stop)
  服务  /record_video/list      interfaces/srv/RecordList
  服务  /record_video/delete    interfaces/srv/RecordDelete
  状态  /record_video/status    interfaces/msg/RecordStatus   (transient_local)
  HTTP  GET /recordings/<file>.fmp4  静态文件播放服务
"""

import http.server
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import subprocess
import threading
import time
from datetime import datetime

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from std_msgs.msg import String

from interfaces.msg import RecordInfo, RecordStatus
from interfaces.srv import RecordControl, RecordDelete, RecordList

# H.264 编码器（仅 ffmpeg 兼容的编码器名）：
# 录像节点用 ffmpeg 直接编码，不使用 GStreamer，因此不能用 mpph264enc（GStreamer 插件名）。
# 另外 Rockchip MPP 通常单实例，video_webrtc_bridge 实时推流已占用 MPP，
# 录像不应争抢硬件编码器，优先 V4L2 M2M，回退 libx264 软编码。
HW_H264_ENCODER = "h264_v4l2m2m"
SOFTWARE_H264_ENCODER = "libx264"

# 文件名白名单：rec_YYYYMMDD_HHMMSS.fmp4
_FILENAME_RE = re.compile(r"^rec_\d{8}_\d{6}\.fmp4$")

GB = 1024 ** 3


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts():
    return time.time()


def _gen_filename():
    """生成 rec_YYYYMMDD_HHMMSS.fmp4，同一秒内重试直到不重名。"""
    base = datetime.now().strftime("rec_%Y%m%d_%H%M%S")
    return base + ".fmp4"


def probe_hw_h264_encoder(timeout=8.0):
    """用 ffmpeg h264_v4l2m2m 试编码判断 V4L2 M2M 硬件编码器是否可用。"""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
        "-t", "0.5", "-c:v", HW_H264_ENCODER, "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
    except FileNotFoundError:
        return False, "未安装 ffmpeg"
    except subprocess.TimeoutExpired:
        return False, "硬件编码试编码超时"
    except Exception as exc:  # noqa: BLE001
        return False, "硬件编码试编码失败: %s" % exc
    return proc.returncode == 0, None


def select_encoder():
    """按 V4L2 M2M -> libx264 顺序选择可用编码器（仅 ffmpeg 兼容的编码器）。

    不使用 Rockchip MPP（mpph264enc 是 GStreamer 插件，ffmpeg 不支持；
    且 MPP 通常单实例，video_webrtc_bridge 实时推流已占用）。
    """
    ok, _ = probe_hw_h264_encoder()
    if ok:
        return HW_H264_ENCODER, "V4L2 M2M 硬件编码"
    return SOFTWARE_H264_ENCODER, "libx264 软件编码"


def probe_audio_input(backend, device, timeout=5.0):
    """试采集 0.3s 音频，判断音频输入是否可用；返回 (ok, cause)。"""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", backend, "-i", device,
        "-t", "0.3", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
    except FileNotFoundError:
        return False, "未安装 ffmpeg"
    except subprocess.TimeoutExpired:
        return False, "音频输入试采集超时"
    except Exception as exc:  # noqa: BLE001
        return False, "音频输入试采集失败: %s" % exc
    if proc.returncode != 0:
        err = proc.stdout.decode("utf-8", "replace").strip()[-300:]
        return False, "音频输入不可用: %s" % err
    return True, None


def _parse_bitrate(text):
    """把 '2M'/'64k'/'800000' 解析成 bps；解析失败返回 0。"""
    text = str(text).strip().lower()
    try:
        if text.endswith("k"):
            return float(text[:-1]) * 1000
        if text.endswith("m"):
            return float(text[:-1]) * 1000 * 1000
        return float(text)
    except (ValueError, IndexError):
        return 0.0


class RecordingSession:
    """一次录像会话：管理 ffmpeg 进程、分片切换、文件写入。"""

    def __init__(self, storage_path, width, height, framerate, bitrate,
                 segment_duration_sec, encoder, logger,
                 audio_enabled=False, audio_backend="pulse",
                 audio_input="default", audio_sample_rate=44100,
                 audio_channels=1, audio_bitrate="64k"):
        self.storage_path = storage_path
        self.width = width
        self.height = height
        self.framerate = framerate
        self.bitrate = bitrate
        self.segment_duration_sec = segment_duration_sec
        self.encoder = encoder
        self.logger = logger

        self.audio_backend = audio_backend
        self.audio_input = audio_input
        self.audio_sample_rate = audio_sample_rate
        self.audio_channels = audio_channels
        self.audio_bitrate = audio_bitrate
        # 运行期可降级：某次分片带音频启动失败后，后续分片只录视频
        self.audio_active = bool(audio_enabled)

        self.proc = None
        self.current_file = None
        self.current_started_ts = 0.0
        self.session_started_ts = 0.0
        self.total_duration_sec = 0
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._segment_thread = None
        self._pump_thread = None

    def _build_ffmpeg_cmd(self, out_path):
        """构建 ffmpeg 命令：rawvideo(+麦克风) -> H.264(+AAC) -> fMP4。"""
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", "%dx%d" % (self.width, self.height),
            "-r", "%.3f" % self.framerate,
            "-i", "-",
        ]
        if self.audio_active:
            # 音频独立输入：默认 PulseAudio（可与 ASR/VAD 共享麦克风），
            # 也可配成 alsa + plughw:X,0（独占，ASR 运行时可能抢不到）
            cmd += ["-f", self.audio_backend, "-i", self.audio_input]
        cmd += ["-map", "0:v"]
        if self.audio_active:
            cmd += ["-map", "1:a"]
        cmd += [
            "-c:v", self.encoder,
            # 强制输出 yuv420p（4:2:0 色度子采样），否则输入是 bgr24 时
            # libx264 默认会输出 yuv444p（H.264 High 4:4:4 Predictive），
            # VLC / 浏览器 / 硬件解码器都不支持，播放会黑屏。
            "-pix_fmt", "yuv420p",
            "-b:v", self.bitrate,
        ]
        if self.encoder == SOFTWARE_H264_ENCODER:
            # High profile + level 4.0：兼容 720p/1080p，桌面/移动/浏览器通吃
            cmd += ["-preset", "ultrafast", "-tune", "zerolatency",
                    "-profile:v", "high", "-level", "4.0"]
        if self.audio_active:
            cmd += [
                "-c:a", "aac", "-b:a", self.audio_bitrate,
                "-ar", str(self.audio_sample_rate),
                "-ac", str(self.audio_channels),
                # 音频与摄像头时钟不同源：async 自动补/丢采样，避免长分片音画漂移
                "-af", "aresample=async=1:first_pts=0",
            ]
        # fMP4 关键参数：每个关键帧一个 fragment，支持边录边播、断电可恢复
        cmd += [
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4",
            out_path,
        ]
        return cmd

    def start(self):
        """启动一次录像会话（第一个分片）。失败返回 False。"""
        os.makedirs(self.storage_path, exist_ok=True)
        self.session_started_ts = _ts()
        if not self._start_segment():
            return False
        self._stop_event.clear()
        self._segment_thread = threading.Thread(
            target=self._segment_loop, daemon=True)
        self._segment_thread.start()
        return True

    def _start_segment(self):
        """启动一个新的 ffmpeg 分片进程。"""
        filename = _gen_filename()
        out_path = os.path.join(self.storage_path, filename)
        # 防止同一秒重名
        suffix = 1
        while os.path.exists(out_path):
            base = os.path.splitext(filename)[0]
            filename = "%s_%d.fmp4" % (base, suffix)
            out_path = os.path.join(self.storage_path, filename)
            suffix += 1

        while True:
            cmd = self._build_ffmpeg_cmd(out_path)
            try:
                self.proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE)
            except (FileNotFoundError, OSError) as exc:
                self.logger.error("启动 ffmpeg 失败: %s" % exc)
                self.proc = None
                return False
            # 启动后等一小段时间检查进程是否存活（编码器/音频设备不可用会立即退出）
            time.sleep(0.2)
            rc = self.proc.poll()
            if rc is None:
                break
            err = b""
            try:
                if self.proc.stderr:
                    err = self.proc.stderr.read() or b""
            except Exception:  # noqa: BLE001
                pass
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None
            err_text = err.decode("utf-8", "replace").strip()[-500:]
            if self.audio_active:
                # 麦克风被独占/掉线时不让整段录像失败：本片起退回仅视频
                self.logger.warning(
                    "带音频启动 ffmpeg 失败（rc=%s），改录纯视频：%s\n命令：%s"
                    % (rc, err_text, " ".join(cmd)))
                self.audio_active = False
                continue
            self.logger.error(
                "ffmpeg 启动后立即退出（rc=%s）：%s\n命令：%s"
                % (rc, err_text, " ".join(cmd)))
            return False
        self.current_file = filename
        self.current_started_ts = _ts()
        self.logger.info(
            "开始录像分片：%s（%dx%d@%.1ffps，编码器=%s，音频=%s）"
            % (filename, self.width, self.height, self.framerate, self.encoder,
               ("%s/%s %dHz %dch" % (self.audio_backend, self.audio_input,
                                     self.audio_sample_rate, self.audio_channels))
               if self.audio_active else "关闭"))
        return True

    def _stop_segment(self, finalize=True):
        """停止当前分片进程，返回是否成功。"""
        if self.proc is None:
            return
        try:
            self.proc.send_signal(signal.SIGINT)
            # ffmpeg 阻塞在 stdin / 音频设备读取上时不会及时处理 SIGINT，
            # 单发 SIGINT 会一直挂到被 kill，fMP4 尾部（moov/fragment）丢失。
            # 立即关闭 stdin 让它读到 EOF，配合 SIGINT 可正常收尾退出。
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.logger.warning("ffmpeg 优雅停止超时，强制终止")
                self.proc.kill()
                self.proc.wait(timeout=2.0)
        except (OSError, subprocess.SubprocessError) as exc:
            self.logger.error("停止 ffmpeg 分片失败: %s" % exc)
        finally:
            self.proc = None

    def _segment_loop(self):
        """按 segment_duration_sec 自动切换分片。"""
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.segment_duration_sec):
                break
            with self.lock:
                if self.proc is None:
                    break
                self._stop_segment()
                if self._stop_event.is_set():
                    break
                self.total_duration_sec += self.segment_duration_sec
                self._start_segment()

    def write_frame(self, data):
        """写入一帧原始数据到 ffmpeg stdin。"""
        with self.lock:
            if self.proc is None or self.proc.stdin is None:
                return
            try:
                self.proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                self.logger.warning("ffmpeg 管道断开，分片可能异常结束")
                self._stop_segment()

    def stop(self):
        """停止整个录像会话。"""
        self._stop_event.set()
        if self._segment_thread and self._segment_thread.is_alive():
            self._segment_thread.join(timeout=10.0)
        with self.lock:
            if self.current_started_ts > 0:
                self.total_duration_sec += int(_ts() - self.current_started_ts)
            self._stop_segment()
        self.logger.info("录像会话结束，累计时长 %ds" % self.total_duration_sec)

    def is_recording(self):
        with self.lock:
            return self.proc is not None

    def current_size_bytes(self):
        with self.lock:
            if not self.current_file:
                return 0
            try:
                return os.path.getsize(
                    os.path.join(self.storage_path, self.current_file))
            except OSError:
                return 0

    def current_duration_sec(self):
        with self.lock:
            if self.current_started_ts <= 0:
                return 0
            return int(_ts() - self.current_started_ts)


class RecordVideoNode(Node):
    def __init__(self):
        super().__init__("record_video_node")

        self.declare_parameter("image_topic", "/image_raw")
        self.declare_parameter("camera_device", "/dev/video0")
        self.declare_parameter("width", 1280)
        self.declare_parameter("height", 720)
        self.declare_parameter("framerate", 30.0)
        self.declare_parameter("storage_path", "")
        self.declare_parameter("storage_limit_gb", 16.0)
        self.declare_parameter("segment_duration_sec", 3600)
        self.declare_parameter("auto_record", True)
        self.declare_parameter("min_free_space_gb", 6.0)
        self.declare_parameter("bitrate", "2M")
        self.declare_parameter("audio_enabled", True)
        self.declare_parameter("audio_backend", "pulse")
        self.declare_parameter("audio_input", "default")
        self.declare_parameter("audio_sample_rate", 44100)
        self.declare_parameter("audio_channels", 1)
        self.declare_parameter("audio_bitrate", "64k")
        self.declare_parameter("http_port", 8088)
        self.declare_parameter("http_host", "0.0.0.0")

        self.image_topic = self.get_parameter("image_topic").value
        self.camera_device = self.get_parameter("camera_device").value
        self.width = int(self.get_parameter("width").value)
        self.height = int(self.get_parameter("height").value)
        self.framerate = float(self.get_parameter("framerate").value)
        self.storage_path = os.path.abspath(os.path.expanduser(
            self.get_parameter("storage_path").value))
        self.storage_limit_gb = float(self.get_parameter("storage_limit_gb").value)
        self.segment_duration_sec = int(self.get_parameter("segment_duration_sec").value)
        self.auto_record = bool(self.get_parameter("auto_record").value)
        self._auto_record_handled = False
        self.min_free_space_gb = float(self.get_parameter("min_free_space_gb").value)
        self.bitrate = self.get_parameter("bitrate").value
        self.audio_enabled = bool(self.get_parameter("audio_enabled").value)
        self.audio_backend = str(
            self.get_parameter("audio_backend").value).strip().lower()
        self.audio_input = str(self.get_parameter("audio_input").value).strip()
        self.audio_sample_rate = int(
            self.get_parameter("audio_sample_rate").value)
        self.audio_channels = int(self.get_parameter("audio_channels").value)
        self.audio_bitrate = self.get_parameter("audio_bitrate").value
        self.http_port = int(self.get_parameter("http_port").value)
        self.http_host = self.get_parameter("http_host").value

        self._cv_bridge = CvBridge()
        self._session = None
        self._session_lock = threading.Lock()

        # 编码器选择（启动时探测一次）
        self.encoder, self.encoder_desc = select_encoder()
        self.get_logger().info("录像编码器：%s" % self.encoder_desc)

        # 音频输入探测（启动时探测一次）：不可用时只录视频，不影响录像功能。
        # 默认 pulse 可与 ASR/VAD 共享麦克风；alsa 直连设备是独占的。
        if self.audio_enabled:
            ok, cause = probe_audio_input(self.audio_backend, self.audio_input)
            if ok:
                self.get_logger().info(
                    "录像音频：%s/%s（%dHz %dch %s）"
                    % (self.audio_backend, self.audio_input,
                       self.audio_sample_rate, self.audio_channels,
                       self.audio_bitrate))
            else:
                self.audio_enabled = False
                self.get_logger().warning(
                    "音频输入不可用，录像将仅包含视频：%s" % cause)

        # 存储路径
        if not self.storage_path:
            self.get_logger().error("storage_path 未配置，录像功能不可用")
        else:
            os.makedirs(self.storage_path, exist_ok=True)
            self.get_logger().info("录像存储路径：%s（上限 %.1f GB）"
                                   % (self.storage_path, self.storage_limit_gb))

        # 回调组：图像回调与服务回调分离
        self._img_group = MutuallyExclusiveCallbackGroup()
        self._srv_group = ReentrantCallbackGroup()

        # 图像订阅按需创建：只在录像期间订阅 /image_raw。
        # 帧大（1280x720x3≈2.6MB），且 rclpy 在进入回调前就会反序列化整帧，
        # 常驻订阅会让空闲进程持续占用 CPU。
        # BEST_EFFORT + depth=1：消费不过来时丢最旧帧，不重传、不积压。
        self._image_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE)
        self._image_sub = None

        # 启动成功后按配置自动录像；transient_local 可接收订阅前发布的 ready
        startup_qos = QoSProfile(
            depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE)
        self.create_subscription(
            String, "/bringup/startup_status", self._startup_status_callback,
            startup_qos, callback_group=self._srv_group)

        # 服务
        self.create_service(
            RecordControl, "/record_video/control", self._handle_control,
            callback_group=self._srv_group)
        self.create_service(
            RecordList, "/record_video/list", self._handle_list,
            callback_group=self._srv_group)
        self.create_service(
            RecordDelete, "/record_video/delete", self._handle_delete,
            callback_group=self._srv_group)

        # 状态 topic（transient_local，晚加入也能读到最新状态）
        status_qos = QoSProfile(
            depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE)
        self._status_pub = self.create_publisher(
            RecordStatus, "/record_video/status", status_qos)

        # 状态定时发布
        self.create_timer(1.0, self._publish_status)

        # HTTP 静态播放服务
        self._http_server = None
        self._http_thread = None
        self._start_http_server()

        self.get_logger().info(
            "record_video_node 就绪：image_topic=%s, storage=%s, http=%s:%d"
            % (self.image_topic, self.storage_path, self.http_host, self.http_port))

    # ---------- HTTP 静态服务 ----------

    def _start_http_server(self):
        node = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # 路径形如 /recordings/<filename>.fmp4
                parts = self.path.split("?", 1)[0].split("/")
                if (len(parts) != 3 or parts[1] != "recordings"
                        or not _FILENAME_RE.match(parts[2])):
                    self.send_error(404, "Not Found")
                    return
                filepath = os.path.join(node.storage_path, parts[2])
                real = os.path.realpath(filepath)
                if not real.startswith(os.path.realpath(node.storage_path) + os.sep):
                    self.send_error(403, "Forbidden")
                    return
                if not os.path.isfile(real):
                    self.send_error(404, "Not Found")
                    return
                size = os.path.getsize(real)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    with open(real, "rb") as f:
                        shutil.copyfileobj(f, self.wfile)
                except (BrokenPipeError, OSError):
                    pass

            def log_message(self, fmt, *args):
                node.get_logger().debug("HTTP %s - %s"
                                        % (self.address_string(), fmt % args))

        try:
            socketserver.TCPServer.allow_reuse_address = True
            self._http_server = socketserver.TCPServer(
                (self.http_host, self.http_port), Handler)
            self._http_thread = threading.Thread(
                target=self._http_server.serve_forever, daemon=True)
            self._http_thread.start()
            self.get_logger().info("HTTP 播放服务已启动：%s:%d/recordings/"
                                   % (self.http_host, self.http_port))
        except OSError as exc:
            self.get_logger().warning("HTTP 播放服务启动失败（端口 %d 被占用？）：%s"
                                      % (self.http_port, exc))

    # ---------- 图像订阅（按需） ----------

    def _ensure_image_subscription(self):
        """录像开始时才订阅 /image_raw。"""
        if self._image_sub is not None:
            return
        self._image_sub = self.create_subscription(
            Image, self.image_topic, self._image_callback,
            self._image_qos, callback_group=self._img_group)

    def _release_image_subscription(self):
        """录像结束后取消订阅，空闲时不收取/反序列化图像。"""
        sub, self._image_sub = self._image_sub, None
        if sub is None:
            return
        try:
            self.destroy_subscription(sub)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning("销毁图像订阅失败: %s" % exc)

    # ---------- 图像回调 ----------

    def _image_callback(self, msg):
        with self._session_lock:
            session = self._session
        if session is None or not session.is_recording():
            return
        try:
            # sensor_msgs/Image -> numpy (BGR8)，直接取原始数据
            if msg.encoding == "bgr8":
                data = bytes(msg.data)
            else:
                cv_img = self._cv_bridge.imgmsg_to_cv2(msg, "bgr8")
                data = cv_img.tobytes()
            session.write_frame(data)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warning("写入录像帧失败: %s" % exc)

    # ---------- 启动成功后自动录像 ----------

    def _startup_status_callback(self, msg):
        if msg.data.strip().lower() != "ready" or self._auto_record_handled:
            return
        self._auto_record_handled = True
        if not self.auto_record:
            self.get_logger().info("Walle 启动就绪，自动录像已禁用")
            return

        request = RecordControl.Request()
        request.action = "start"
        request.segment_sec = 0
        response = self._handle_control(request, RecordControl.Response())
        if response.result == 0:
            self.get_logger().info(
                "Walle 启动就绪，已自动开始录像：%s" % response.filename)
        elif response.result == -4:
            self.get_logger().info(
                "Walle 启动就绪，录像已在进行：%s" % response.filename)
        else:
            self.get_logger().error("自动开始录像失败：%s" % response.cause)

    # ---------- 服务：开始 / 停止 ----------

    def _handle_control(self, request, response):
        action = request.action.strip().lower()
        with self._session_lock:
            if action == "start":
                if self._session is not None and self._session.is_recording():
                    response.result = -4
                    response.cause = "已在录制"
                    response.filename = self._session.current_file or ""
                    return response
                # 磁盘空间检查
                free = self._free_space_gb()
                if free < self.min_free_space_gb:
                    response.result = -7
                    response.cause = ("磁盘空间不足：剩余 %.1f GB，最低要求 %.1f GB"
                                      % (free, self.min_free_space_gb))
                    return response
                segment_sec = (request.segment_sec
                               if request.segment_sec > 0
                               else self.segment_duration_sec)
                try:
                    self._session = RecordingSession(
                        self.storage_path, self.width, self.height,
                        self.framerate, self.bitrate, segment_sec,
                        self.encoder, self.get_logger(),
                        audio_enabled=self.audio_enabled,
                        audio_backend=self.audio_backend,
                        audio_input=self.audio_input,
                        audio_sample_rate=self.audio_sample_rate,
                        audio_channels=self.audio_channels,
                        audio_bitrate=self.audio_bitrate)
                    if not self._session.start():
                        response.result = -6
                        response.cause = "启动录像失败"
                        self._session = None
                        return response
                    self._enforce_quota()
                    self._ensure_image_subscription()
                    response.result = 0
                    response.cause = "ok"
                    response.filename = self._session.current_file or ""
                    response.started_at = _now_str()
                    response.started_ts = float(self._session.session_started_ts)
                except Exception as exc:  # noqa: BLE001
                    response.result = -6
                    response.cause = "启动录像失败: %s" % exc
                    self._session = None
                return response

            elif action == "stop":
                if self._session is None or not self._session.is_recording():
                    response.result = -4
                    response.cause = "当前未录制"
                    return response
                filename = self._session.current_file or ""
                try:
                    self._session.stop()
                except Exception as exc:  # noqa: BLE001
                    self.get_logger().warning("停止录像异常: %s" % exc)
                self._session = None
                self._release_image_subscription()
                response.result = 0
                response.cause = "ok"
                response.filename = filename
                return response
            else:
                response.result = -1
                response.cause = "未知 action: %s（仅支持 start/stop）" % action
                return response

    # ---------- 服务：列表 ----------

    def _handle_list(self, request, response):
        try:
            records = self._list_records()
            response.result = 0
            response.cause = "ok"
            response.records = records
            response.used_bytes = self._dir_size_bytes()
            response.limit_bytes = int(self.storage_limit_gb * GB)
        except Exception as exc:  # noqa: BLE001
            response.result = -6
            response.cause = "查询录像列表失败: %s" % exc
            response.records = []
            response.used_bytes = 0
            response.limit_bytes = int(self.storage_limit_gb * GB)
        return response

    def _list_records(self):
        records = []
        if not os.path.isdir(self.storage_path):
            return records
        # 取当前正在录制的文件名，用于在列表中标记 recording=true
        with self._session_lock:
            current = self._session.current_file if self._session else None
        for name in os.listdir(self.storage_path):
            if not name.endswith(".fmp4"):
                continue
            path = os.path.join(self.storage_path, name)
            if not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            info = RecordInfo()
            info.filename = name
            info.path = path
            info.size_bytes = int(st.st_size)
            info.created_at = datetime.fromtimestamp(st.st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S")
            info.duration_sec = self._estimate_duration_sec(name, st.st_size)
            info.recording = (current is not None and name == current)
            records.append(info)
        records.sort(key=lambda r: r.filename, reverse=True)
        return records

    def _estimate_duration_sec(self, filename, size_bytes):
        """根据文件名时间戳估算时长：当前正在录的用实际时长，历史文件按码率估算。"""
        with self._session_lock:
            session = self._session
        if session is not None and session.current_file == filename:
            return session.current_duration_sec()
        # 历史文件按码率粗估（bitrate 如 2M -> 2*1024*1024/8 字节/秒）
        try:
            bps = _parse_bitrate(self.bitrate)
            if self.audio_enabled:
                bps += _parse_bitrate(self.audio_bitrate)
            bytes_per_sec = bps / 8.0
            if bytes_per_sec > 0:
                return int(size_bytes / bytes_per_sec)
        except (ValueError, IndexError):
            pass
        return 0

    # ---------- 服务：删除 ----------

    def _handle_delete(self, request, response):
        filename = request.filename.strip()
        response.filename = filename
        if not _FILENAME_RE.match(filename):
            response.result = -2
            response.cause = "非法文件名"
            return response
        filepath = os.path.join(self.storage_path, filename)
        real = os.path.realpath(filepath)
        if not real.startswith(os.path.realpath(self.storage_path) + os.sep):
            response.result = -2
            response.cause = "非法路径"
            return response
        with self._session_lock:
            if (self._session is not None
                    and self._session.current_file == filename):
                response.result = -4
                response.cause = "该文件正在录制，不可删除"
                return response
        if not os.path.isfile(real):
            response.result = -3
            response.cause = "文件不存在"
            return response
        try:
            os.remove(real)
            response.result = 0
            response.cause = "ok"
        except OSError as exc:
            response.result = -6
            response.cause = "删除失败: %s" % exc
        return response

    # ---------- 存储空间管理 ----------

    def _free_space_gb(self):
        try:
            usage = shutil.disk_usage(self.storage_path)
            return usage.free / GB
        except OSError:
            return 0.0

    def _dir_size_bytes(self):
        total = 0
        if not os.path.isdir(self.storage_path):
            return 0
        for name in os.listdir(self.storage_path):
            path = os.path.join(self.storage_path, name)
            if os.path.isfile(path):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return total

    def _enforce_quota(self):
        """按 FIFO 回收最旧文件，直到目录占用低于上限的 90%。"""
        limit_bytes = int(self.storage_limit_gb * GB)
        target_bytes = int(limit_bytes * 0.9)
        used = self._dir_size_bytes()
        if used <= limit_bytes:
            return
        files = []
        with self._session_lock:
            current = self._session.current_file if self._session else None
        for name in os.listdir(self.storage_path):
            if not name.endswith(".fmp4"):
                continue
            if name == current:
                continue
            path = os.path.join(self.storage_path, name)
            try:
                files.append((os.path.getmtime(path), name, os.path.getsize(path)))
            except OSError:
                continue
        files.sort()  # 按 mtime 升序（最旧在前）
        for _, name, size in files:
            if used <= target_bytes:
                break
            try:
                os.remove(os.path.join(self.storage_path, name))
                used -= size
                self.get_logger().info("回收录像文件（空间超限）：%s（%.1f MB）"
                                       % (name, size / 1024 / 1024))
            except OSError as exc:
                self.get_logger().warning("回收文件失败 %s: %s" % (name, exc))

    # ---------- 状态发布 ----------

    def _publish_status(self):
        msg = RecordStatus()
        with self._session_lock:
            session = self._session
            if session is not None and session.is_recording():
                msg.recording = True
                msg.filename = session.current_file or ""
                msg.started_at = datetime.fromtimestamp(
                    session.session_started_ts).strftime("%Y-%m-%d %H:%M:%S")
                msg.started_ts = float(session.session_started_ts)
                msg.current_duration_sec = session.current_duration_sec()
                msg.total_duration_sec = (
                    session.total_duration_sec + msg.current_duration_sec)
                msg.current_size_bytes = session.current_size_bytes()
            else:
                msg.recording = False
        msg.used_bytes = self._dir_size_bytes()
        msg.limit_bytes = int(self.storage_limit_gb * GB)
        self._status_pub.publish(msg)

    # ---------- 节点退出 ----------

    def destroy_node(self):
        with self._session_lock:
            if self._session is not None:
                try:
                    self._session.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._session = None
        self._release_image_subscription()
        if self._http_server is not None:
            try:
                self._http_server.shutdown()
            except Exception:  # noqa: BLE001
                pass
        super().destroy_node()


def main():
    rclpy.init()
    node = RecordVideoNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
