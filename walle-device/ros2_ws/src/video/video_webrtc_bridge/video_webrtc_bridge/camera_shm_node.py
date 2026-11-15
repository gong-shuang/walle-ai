#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""walle_camera：独占摄像头，发布 /image_raw，并通过共享内存输出 H.264。

最小版本：
  v4l2src(MJPG) -> mppjpegdec(NV12) -> tee
    ├─ 10fps -> RGB -> appsink -> /image_raw
    └─ mpph264enc -> h264parse -> shmsink（供 video_webrtc_bridge 推流）
"""

import os
import threading
import time

import gi
import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from sensor_msgs.msg import Image

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)


class CameraShmNode(Node):
    def __init__(self):
        super().__init__("walle_camera")
        self.device = self.declare_parameter("camera_device", "/dev/video0").value
        self.camera_format = str(
            self.declare_parameter("camera_format", "MJPG").value).strip().upper()
        if self.camera_format == "MJPEG":
            self.camera_format = "MJPG"
        if self.camera_format not in ("MJPG", "YUYV"):
            self.get_logger().warning(
                "camera_format=%s 不支持，按 MJPG 处理" % self.camera_format)
            self.camera_format = "MJPG"
        self.width = int(self.declare_parameter("camera_width", 640).value)
        self.height = int(self.declare_parameter("camera_height", 480).value)
        self.fps = float(self.declare_parameter("framerate", 30.0).value)
        self.ros_fps = float(self.declare_parameter("ros_image_fps", 10.0).value)
        self.bitrate = self.declare_parameter("video_bitrate", "1000k").value
        self.gop = int(self.declare_parameter("video_gop", 30).value)
        self.shm_socket = self.declare_parameter(
            "shm_socket", "/tmp/walle-video.sock").value
        self.image_topic = self.declare_parameter("image_topic", "/image_raw").value
        # 无帧看门狗：v4l2src/MPP/编码链卡死时 GStreamer 不会报 bus error，
        # 只能靠长时间没有新帧来判断并重建管道
        self.frame_timeout = float(
            self.declare_parameter("frame_timeout", 5.0).value)

        self._pipeline = None
        self._lock = threading.Lock()
        self._restarting = False
        self._last_sample_ts = time.monotonic()
        self._last_encoded_ts = time.monotonic()
        self._shm_client = False
        self._image_pub = self.create_publisher(
            Image, self.image_topic, QoSProfile(
                depth=2,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.VOLATILE,
                history=HistoryPolicy.KEEP_LAST,
            ))
        self._start_pipeline()
        self.create_timer(1.0, self._poll_bus)
        self.get_logger().info(
            "walle_camera 启动：%s %s %dx%d@%g，/image_raw=%dfps，H.264 shm=%s"
            % (self.device, self.camera_format, self.width, self.height,
               self.fps, min(int(self.ros_fps), int(round(self.fps))),
               self.shm_socket))

    def _bitrate_bps(self, value, default):
        text = str(value or "").strip().lower()
        try:
            if text.endswith("k"):
                return int(float(text[:-1]) * 1000)
            if text.endswith("m"):
                return int(float(text[:-1]) * 1000 * 1000)
            return int(float(text))
        except ValueError:
            return default

    def _pipeline_desc(self):
        fps = max(int(round(self.fps)), 1)
        ros_fps = min(max(int(self.ros_fps), 1), fps)
        drop = 0.0
        if ros_fps < fps:
            drop = min(1.0 - float(ros_fps) / fps, 0.95)
        drop_el = ("identity drop-probability=%.4f ! " % drop) if drop > 0 else ""
        bps = self._bitrate_bps(self.bitrate, 1000 * 1000)
        if self.camera_format == "YUYV":
            source = (
                "v4l2src device=%s io-mode=2 ! "
                "video/x-raw,format=YUY2,width=%d,height=%d,framerate=%d/1 ! "
                % (self.device, self.width, self.height, fps))
            encode_pre = "videoconvert ! video/x-raw,format=NV12 ! "
        else:
            source = (
                "v4l2src device=%s io-mode=2 ! "
                "image/jpeg,width=%d,height=%d,framerate=%d/1 ! "
                "jpegparse ! mppjpegdec format=NV12 ! "
                % (self.device, self.width, self.height, fps))
            encode_pre = ""
        encode = (
            "mpph264enc bps=%d rc-mode=cbr gop=%d ! h264parse ! "
            # 必须保持 wait-for-connection=true（默认值）：shmsink 只在有客户端
            # 连接时才下发 caps，设成 false 时客户端永远收不到 caps，
            # h264parse 会一直等待、一帧都不输出（实测 0 帧）。
            # 断开后阻塞在这里也不会反压摄像头：上一级 queue 是 leaky 的。
            "shmsink name=shm_sink socket-path=%s sync=false"
            % (bps, max(self.gop, 1), self.shm_socket)
        )
        return (
            source
            + "tee name=t "
            + "t. ! queue max-size-buffers=1 leaky=downstream ! "
            + drop_el
            + "videoconvert ! video/x-raw,format=RGB ! "
            + "appsink name=ros_sink emit-signals=true max-buffers=1 "
            + "drop=true sync=false "
            # leaky=downstream：推流分支任何卡顿（客户端消失/编码卡死）
            # 都不能反压 tee，否则整个摄像头管道（含 /image_raw）会一起卡死
            + "t. ! queue max-size-buffers=2 leaky=downstream ! "
            + encode_pre
            + encode
        )

    def _start_pipeline(self):
        try:
            pipeline = Gst.parse_launch(self._pipeline_desc())
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error("创建摄像头管道失败: %s" % exc)
            return False
        sink = pipeline.get_by_name("ros_sink")
        sink.connect("new-sample", self._on_sample)
        shm_sink = pipeline.get_by_name("shm_sink")
        if shm_sink is not None:
            pad = shm_sink.get_static_pad("sink")
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.BUFFER, self._on_encoded_buffer)
            # 没有客户端时 shmsink 会阻塞等待（wait-for-connection=true），
            # 编码分支本来就不出帧，看门狗不能把它当成卡死
            shm_sink.connect("client-connected", self._on_shm_client_connected)
            shm_sink.connect("client-disconnected", self._on_shm_client_disconnected)
        self._shm_client = False
        with self._lock:
            self._pipeline = pipeline
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.get_logger().error("摄像头管道启动失败（%s）" % self.device)
            pipeline.set_state(Gst.State.NULL)
            with self._lock:
                self._pipeline = None
            return False
        self.get_logger().info("摄像头管道已启动，H.264 写入 %s" % self.shm_socket)
        self._last_sample_ts = time.monotonic()
        self._last_encoded_ts = time.monotonic()
        return True

    def _on_encoded_buffer(self, pad, info):
        self._last_encoded_ts = time.monotonic()
        return Gst.PadProbeReturn.OK

    def _on_shm_client_connected(self, sink, client_id):
        self._shm_client = True
        self._last_encoded_ts = time.monotonic()
        self.get_logger().info("推流客户端已连接共享内存（id=%s）" % client_id)

    def _on_shm_client_disconnected(self, sink, client_id):
        self._shm_client = False
        self.get_logger().info(
            "推流客户端已断开共享内存（id=%s），重建管道以重新下发 caps" % client_id)
        # shmsink 只在管道首次出帧时把 caps 写入共享内存，之后的客户端只能拿到
        # 裸 buffer、拿不到 caps，h264parse 会一直等待（表现为第二次推流黑屏）。
        # 唯一可靠的解法是断流后重建管道，让新管道的 caps 回到写入起点。
        # 注意：这里是 GStreamer 流线程，不能直接改管道状态（会死锁），
        # 交给独立线程延迟处理
        threading.Thread(
            target=self._restart_after_disconnect, daemon=True).start()

    def _restart_after_disconnect(self):
        time.sleep(0.1)
        self._schedule_restart(delay=0.0)

    def _on_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        self._last_sample_ts = time.monotonic()
        buf = sample.get_buffer()
        caps = sample.get_caps()
        if buf is None or caps is None:
            return Gst.FlowReturn.OK
        s = caps.get_structure(0)
        width = int(s.get_value("width"))
        height = int(s.get_value("height"))
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)
        msg = Image()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "camera"
        msg.width = width
        msg.height = height
        msg.encoding = "rgb8"
        msg.is_bigendian = 0
        msg.step = width * 3
        msg.data = data
        self._image_pub.publish(msg)
        return Gst.FlowReturn.OK

    def _poll_bus(self):
        with self._lock:
            pipeline = self._pipeline
        if pipeline is None:
            return
        bus = pipeline.get_bus()
        while bus.have_pending():
            msg = bus.pop()
            if msg.type in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
                if msg.type == Gst.MessageType.ERROR:
                    err, _ = msg.parse_error()
                    self.get_logger().error("摄像头管道异常：%s" % err.message)
                else:
                    self.get_logger().warning("摄像头管道 EOS，准备重启")
                self._schedule_restart()
                return
        # 无帧看门狗：管道线程卡死（无 bus error）时只能靠出帧超时发现。
        # /image_raw 分支必须一直有帧；H.264 分支只有客户端连接时才应该出帧，
        # 没有客户端时 shmsink 会阻塞（正常现象），不能误判
        if self.frame_timeout > 0:
            idle = time.monotonic() - self._last_sample_ts
            if self._shm_client:
                idle = max(idle, time.monotonic() - self._last_encoded_ts)
            if idle > self.frame_timeout:
                self.get_logger().error(
                    "摄像头已 %.1fs 没有输出帧，重建管道" % idle)
                self._last_sample_ts = time.monotonic()
                self._last_encoded_ts = time.monotonic()
                self._schedule_restart()

    def _schedule_restart(self, delay=2.0):
        with self._lock:
            if self._restarting:
                return
            self._restarting = True
            pipeline = self._pipeline
            self._pipeline = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        threading.Thread(target=self._restart, args=(delay,), daemon=True).start()

    def _restart(self, delay=2.0):
        try:
            while rclpy.ok():
                time.sleep(delay)
                delay = 2.0
                if self._start_pipeline():
                    return
        finally:
            with self._lock:
                self._restarting = False


def main():
    rclpy.init()
    node = CameraShmNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        with node._lock:
            pipeline = node._pipeline
            node._pipeline = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
