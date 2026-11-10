#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""net_mqtt_bridge

MQTT 协议入口（唯一 MQTT 客户端）：
  - 保留原 linux-demo/main.py 的 online/LWT、discovery、state、cmd、capability、alarm、
    phone/audio 全部逻辑；
  - video/start|stop 不再直接起 ffmpeg，而是通过 ROS2 service 调
    video_webrtc_bridge；
  - 订阅 video_webrtc_bridge 的推流状态，填 state/result 的 video 分组；
  - 以 Bool 心跳告知 video_webrtc_bridge 当前 MQTT 是否在线。
"""

import logging
import os
import re
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool

from interfaces.msg import VideoStreamStatus
from interfaces.srv import VideoStreamControl


class NetMqttBridge(Node):
    def __init__(self):
        super().__init__('net_mqtt_bridge')

        self.declare_parameter('env_file', '/etc/walle-ai.conf')
        self.declare_parameter('device_id_file', '')
        self.declare_parameter('video_service', '/video_webrtc_bridge/stream_control')
        self.declare_parameter('video_status_topic', '/video_webrtc_bridge/status')
        self.declare_parameter('video_call_timeout', 15.0)
        self.declare_parameter('video_startup_grace', 3.0)

        env_file = self.get_parameter('env_file').value or ''
        if env_file:
            os.environ['RPI_MONITOR_ENV'] = env_file
        device_id_param = self.get_parameter('device_id_file').value or ''

        # paho-mqtt 2.x 才有 CallbackAPIVersion；缺失时给出可操作提示
        try:
            import paho.mqtt.client as mqtt
            if not hasattr(mqtt, 'CallbackAPIVersion'):
                raise RuntimeError('paho-mqtt 版本过低，需要 >= 2.0')
        except Exception as exc:  # noqa: BLE001
            self.get_logger().fatal(
                'MQTT 依赖不可用: %s（请安装 paho-mqtt>=2.0）' % exc)
            raise

        # 导入 agent 时它会加载 env_file，DEVICE_ID_FILE 可能来自环境文件。
        # 参数优先级最高；都没有时再回退到旧安装目录 / 用户配置目录。
        import net_mqtt_bridge.agent as agent
        import net_mqtt_bridge.config as config

        # P0-1.4：MQTT 必填项缺失 / 明文配置时直接拒绝启动，避免带病上线
        problems = agent.validate_mqtt_config()
        if problems:
            for problem in problems:
                self.get_logger().fatal('MQTT 配置错误: %s' % problem)
            raise RuntimeError(
                'MQTT 配置不完整，拒绝启动（请检查 /etc/walle-ai.conf）')

        if device_id_param:
            device_id_file = device_id_param
        elif config.DEVICE_ID_FILE:
            device_id_file = config.DEVICE_ID_FILE
        else:
            legacy = os.path.expanduser('~/walle_linux/.device-id')
            if os.path.isfile(legacy):
                device_id_file = legacy
                self.get_logger().info('复用旧设备标识: %s' % legacy)
            else:
                # 不再放在 install 目录（重编译会丢）
                device_id_file = os.path.expanduser(
                    '~/.config/walle-ai/device-id')
        os.environ['DEVICE_ID_FILE'] = device_id_file
        config.DEVICE_ID_FILE = device_id_file

        self._agent_mod = agent
        self.agent = agent.RpiMonitor()

        self._video_status_msg = None
        self._status_lock = threading.Lock()
        self._shutting_down = False
        self._agent_thread = None
        self._last_heartbeat_log = 0.0

        self._video_client = self.create_client(
            VideoStreamControl,
            self.get_parameter('video_service').value)

        status_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
        )
        self.create_subscription(
            VideoStreamStatus,
            self.get_parameter('video_status_topic').value,
            self._on_video_status,
            status_qos)

        self._heartbeat_pub = self.create_publisher(Bool, '~/heartbeat', 10)
        self.create_timer(1.0, self._publish_heartbeat)

        # 用 ROS 服务替换 main.py 里的 ffmpeg 推流实现
        self.agent.handle_video_request = self._handle_video_start
        self.agent.handle_video_stop = self._handle_video_stop
        self.agent._video_status = self._video_status_dict
        self.agent._stop_video_stream = self._stop_video_via_service

        self.get_logger().info(
            'net_mqtt_bridge 启动：device_id=%s tenant=%s video_service=%s'
            % (config.DEVICE_ID, agent.sanitize_segment(config.MQTT_TENANT),
               self.get_parameter('video_service').value))

    # ------------------------------------------------------------------ 生命周期
    def start_agent(self):
        self._agent_thread = threading.Thread(
            target=self._run_agent, daemon=True, name='mqtt-agent')
        self._agent_thread.start()

    def _run_agent(self):
        try:
            self.agent.run()
        except Exception:  # noqa: BLE001
            logging.exception('MQTT agent 线程异常退出')
            # 让 launch 的 respawn 接管，避免进程活着但 MQTT 永远断线
            try:
                rclpy.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self):
        self._shutting_down = True
        self.agent.stop_event.set()
        if (self._agent_thread is not None
                and self._agent_thread.is_alive()
                and self._agent_thread is not threading.current_thread()):
            self._agent_thread.join(timeout=5.0)

    # ------------------------------------------------------------------ 心跳/状态
    def _publish_heartbeat(self):
        client = self.agent.client
        connected = bool(client is not None and client.is_connected())
        self._heartbeat_pub.publish(Bool(data=connected))

    def _on_video_status(self, msg):
        with self._status_lock:
            self._video_status_msg = msg

    def _video_status_dict(self):
        """替换 RpiMonitor._video_status：数据来自 video_webrtc_bridge 的状态 topic。"""
        with self._status_lock:
            st = self._video_status_msg
        if st is None or not st.streaming:
            return {
                'streaming': False,
                'room_number': None,
                'video_device': None,
                'audio': None,
                'pid': None,
                'started_at': None,
                'uptime_sec': None,
            }
        audio = {
            'available': bool(st.audio_available),
            'device': st.audio_device or None,
            'codec': st.audio_codec or None,
            'bitrate': st.audio_bitrate or None,
            'reason': st.audio_reason or None,
        }
        return {
            'streaming': True,
            'room_number': st.room_number or None,
            'video_device': st.video_device or None,
            'audio': audio,
            'pid': int(st.pid) if st.pid else None,
            'started_at': st.started_at or None,
            'uptime_sec': (round(time.time() - st.started_ts, 1)
                           if st.started_ts > 0 else None),
        }

    # ------------------------------------------------------------------ 服务调用
    def _call_video(self, request):
        if not self._video_client.service_is_ready():
            try:
                # 开机阶段视频节点可能还没起来，给 2 秒宽限
                self._video_client.wait_for_service(timeout_sec=2.0)
            except Exception:  # noqa: BLE001
                pass
        if not self._video_client.service_is_ready():
            return None

        future = self._video_client.call_async(request)
        done = threading.Event()
        box = {}

        def _done(fut):
            try:
                box['response'] = fut.result()
            except Exception as exc:  # noqa: BLE001
                box['error'] = exc
            done.set()

        future.add_done_callback(_done)
        timeout = float(self.get_parameter('video_call_timeout').value)
        if not done.wait(timeout):
            future.cancel()
            return None
        return box.get('response')

    # ------------------------------------------------------------------ 推流请求
    def _handle_video_start(self, payload):
        ok, data = self.agent.verify_uuid(
            payload, self._agent_mod.REQ_VIDEO_START)
        if not ok:
            return

        video_device = str(data.get('video_device') or '').strip()
        room_number = str(data.get('room_number') or '').strip()
        req = self.agent._video_req(data, video_device, room_number)

        if not self._agent_mod.VIDEO_ENABLE:
            self.agent._publish_video_result(
                req, -2, '命令执行失败',
                {'error': '推流能力已关闭（VIDEO_ENABLE=False）'})
            return
        if (not re.match(r'^/dev/video\d+$', video_device)
                or not os.path.exists(video_device)):
            self.agent._publish_video_result(
                req, -1, '没有此摄像头设备',
                {'error': '设备节点不存在: %s' % (video_device or '<空>')})
            return
        if not re.match(self._agent_mod.VIDEO_ROOM_PATTERN, room_number):
            self.agent._publish_video_result(
                req, -2, '命令执行失败',
                {'error': 'room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）'})
            return
        if not self._agent_mod.rtmp_secret_ready():
            self.agent._publish_video_result(
                req, -2, '命令执行失败',
                {'error': '未配置 Oryx 推流 secret（请在 /etc/walle-ai.conf 设置 MEDIA_SERVER_SECRET）'})
            return

        srv_req = VideoStreamControl.Request()
        srv_req.start = True
        srv_req.video_device = video_device
        srv_req.room_number = room_number
        resp = self._call_video(srv_req)
        if resp is None:
            self.agent._publish_video_result(
                req, -2, '命令执行失败',
                {'action': 'failed',
                 'error': '视频推流服务不可用（video_webrtc_bridge 未启动或未就绪）'})
            return

        extra = {
            'action': resp.action or ('failed' if resp.result != 0 else 'started'),
            'rtmp_url': resp.rtmp_url or None,
            'command': resp.command or None,
            'pid': int(resp.pid) if resp.pid > 0 else None,
            'started_at': resp.started_at or None,
            'startup_grace_sec': float(
                self.get_parameter('video_startup_grace').value),
            'audio': {
                'available': bool(resp.audio_available),
                'device': resp.audio_device or None,
                'codec': resp.audio_codec or None,
                'bitrate': resp.audio_bitrate or None,
                'reason': resp.audio_reason or None,
            },
            'video_device': video_device,
            'room_number': room_number,
        }
        if resp.started_ts > 0:
            extra['uptime_sec'] = round(time.time() - resp.started_ts, 1)
        if resp.action == 'already_streaming':
            extra['note'] = '相同 video_device 和 room_number 已在推流，本次不做处理'
        if resp.action == 'restarted':
            extra['previous_room_number'] = resp.previous_room_number or None
            extra['previous_video_device'] = resp.previous_video_device or None
        if resp.result != 0:
            extra['action'] = resp.action or 'failed'
            extra['error'] = resp.error or resp.cause or '未知错误'

        self.agent._publish_video_result(
            req, int(resp.result),
            resp.cause or ('ok' if resp.result == 0 else '命令执行失败'),
            extra)

    def _handle_video_stop(self, payload):
        ok, data = self.agent.verify_uuid(
            payload, self._agent_mod.REQ_VIDEO_STOP)
        if not ok:
            return

        rid = self.agent._rid(data)
        req_room = str(data.get('room_number') or '').strip()
        srv_req = VideoStreamControl.Request()
        srv_req.start = False
        srv_req.room_number = req_room
        resp = self._call_video(srv_req)

        if resp is None:
            self.agent._publish_video_stop_result(
                rid, -1, '当前没有设备在推流',
                {'room_number': req_room or None, 'stopped': False,
                 'error': '视频推流服务不可用（video_webrtc_bridge 未启动或未就绪）'})
            return

        extra = {
            'room_number': resp.room_number or req_room or None,
            'video_device': resp.video_device or None,
            'pid': int(resp.pid) if resp.pid > 0 else None,
            'started_at': resp.started_at or None,
            'audio': {
                'available': bool(resp.audio_available),
                'device': resp.audio_device or None,
                'codec': resp.audio_codec or None,
                'bitrate': resp.audio_bitrate or None,
                'reason': resp.audio_reason or None,
            },
            'stopped': bool(resp.stopped),
        }
        if resp.result != 0:
            extra['error'] = resp.error or resp.cause or '当前没有设备在推流'

        self.agent._publish_video_stop_result(
            rid, int(resp.result),
            resp.cause or ('ok' if resp.result == 0 else '当前没有设备在推流'),
            extra)

    def _stop_video_via_service(self, reason=''):
        """替换 RpiMonitor._stop_video_stream：程序退出时尽量通知视频节点停流。"""
        if self._shutting_down:
            # 退出阶段 executor 已停止，服务调不通；由心跳超时让视频节点自停
            return False
        srv_req = VideoStreamControl.Request()
        srv_req.start = False
        srv_req.room_number = ''
        resp = self._call_video(srv_req)
        return bool(resp is not None and resp.result == 0 and resp.stopped)


def main(args=None):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    rclpy.init(args=args)
    node = None
    executor = None
    try:
        node = NetMqttBridge()
        node.start_agent()
        executor = MultiThreadedExecutor(num_threads=3)
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


if __name__ == '__main__':
    main()
