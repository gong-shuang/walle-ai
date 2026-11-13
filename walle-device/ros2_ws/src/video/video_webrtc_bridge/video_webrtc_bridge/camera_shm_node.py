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

        self._pipeline = None
        self._lock = threading.Lock()
        self._restarting = False
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
            "shmsink socket-path=%s sync=false wait-for-connection=true"
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
            + "t. ! queue max-size-buffers=2 ! "
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
        with self._lock:
            self._pipeline = pipeline
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.get_logger().error("摄像头管道启动失败（%s）" % self.device)
            pipeline.set_state(Gst.State.NULL)
            with self._lock:
                self._pipeline = None
            return False
        self.get_logger().info("摄像头管道已启动，H.264 写入 %s" % self.shm_socket)
        return True

    def _on_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
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

    def _schedule_restart(self):
        with self._lock:
            if self._restarting:
                return
            self._restarting = True
            pipeline = self._pipeline
            self._pipeline = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        threading.Thread(target=self._restart, daemon=True).start()

    def _restart(self):
        try:
            while rclpy.ok():
                time.sleep(2.0)
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
