#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RaspberryPi 系统监控 Agent（基于 paho-mqtt 2.x）

功能：
1. 周期巡检，异常实时上报（USB 插拔 / CPU / 内存 / 磁盘 / 带宽 / 温度），带状态机：
   越限才报，恢复后发 recover 事件，不会重复刷屏。
2. 响应设备身份查询、状态查询、摄像头能力查询、推流/停止推流。
3. 上线/下线：retained + 遗嘱（LWT），App 重启或重连后能立刻看到设备在线状态。

协议：多租户主题
    所有主题都在 walle/{tenant}/device/{device}/ 下展开，租户 = MQTT_TENANT（与手机 App 登录用户名一致），
    device 用稳定的 device_id：首次启动按「MAC + CPU 核心数 + 内存 GiB」算 md5 前 16 位，
    之后落盘复用（resolve_device_id），不会因为 MemTotal 的 KiB 级浮动而漂移。

    请求（App -> 设备）                          应答（设备 -> App）                      说明
    ------------------------------------------------------------------------------------------------
    —                                          .../online                          主动上报（retained + LWT）
    —                                          .../alarm/report                    主动上报（告警 / USB）
    walle/{t}/discovery/query/request           .../discovery/query/response         设备发现（租户级广播）
    .../baseState/query/request                     .../baseState/query/response             状态快照
    .../runState/query/request                 .../runState/query/response          运行状态（能力 + 推流/录像/车控/传感器）
    .../video/start/request                     .../video/start/response             开始推流（动作）
    .../video/stop/request                      .../video/stop/response              停止推流（动作）
    .../phone/audio/send/request                .../phone/audio/send/response        手机麦上行·开（App 发起）
    .../phone/audio/stop/request                .../phone/audio/stop/response        手机麦上行·关（App 发起）
    .../car/motor/set/request                   .../car/motor/set/response           电机持续控制（最新状态覆盖，每条请求都有应答）

    命名约定：请求-应答对的请求 .../request、应答 .../response；
    持续设置且不需要应答的 .../cmd；主动上报为 online 与 alarm/report。

    除 discovery 广播外，设备端用按层数的 `+` 模式只订阅请求/持续控制主题，
    不订 `#` 整棵子树（避免收到自己发布的 response/report）。

安全：请求按主题第 4 段的 device_id 路由，Broker 侧按
      walle/{tenant}/device/${clientid}/# 授权，非本设备的报文不会到达。

依赖：pip install -r requirements.txt  （paho-mqtt>=2.0、psutil）
     摄像头能力探测需 v4l-utils，推流需 ffmpeg。

配置：全部通过环境变量；前台运行时会自动加载 /etc/walle-ai.conf，
      查找顺序 --env-file > $RPI_MONITOR_ENV > /etc/walle-ai.conf > ./walle-ai.conf，
      已存在的 shell 变量优先。必填 MQTT_HOST / MQTT_USERNAME / MQTT_PASSWORD / MQTT_TENANT，
      TLS 默认开启（默认 8883）；仅 debug 环境允许显式配置明文连接。

命令行：
    python3 main.py                         前台运行（自动读 /etc/walle-ai.conf）
    python3 main.py --check                 只打印解析后的配置与订阅主题，不连 broker
    python3 main.py --env-file PATH         使用指定配置文件
    python3 main.py --help                  查看本说明

部署：见 linux-demo/README.md「首次部署」（systemd 单元为 walle-ai.service）

模块拆分（P2-2.1）：
    config.py       环境文件、MQTT 配置、主题与运行期常量
    identity.py     设备身份、网络信息与格式化
    monitor.py      系统采样、阈值告警与 baseState/query/response 快照
    protocol.py     MQTT 回调、请求分发、身份/状态查询
    capability.py   外设能力探测与缓存
    media.py        摄像头探测、ffmpeg 参数与日志
    audio.py        音频能力探测
    video.py        video/start|stop（ROS 下由 node.py 覆盖为服务调用）
    phone_audio.py  phone/audio/* 手机麦上行
本文件保留 RpiMonitor 组合、启动流程和 --check，并 re-export 原有公共名字。
"""

if __name__ == "__main__" and __package__ in (None, ""):  # pragma: no cover
    # 兼容 `python3 agent.py --check`：以包方式重新导入后再执行
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from net_mqtt_bridge.agent import main as _main

    _sys.exit(_main())

import json
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import paho.mqtt.client as mqtt
import psutil

from . import config
from .alarm_sensors import SensorAlarmMixin
from .capability import CapabilityMixin
from .car_motor import CarMotorMixin
from .car_servo import CarServoMixin
from .control import ControlLease, ControlMixin
from .config import (ACTION_REQUESTS, MQTT_CA_FILE, MQTT_HOST, MQTT_KEEPALIVE,  # noqa: F401
                     MQTT_PASSWORD, MQTT_PORT, MQTT_QOS, MQTT_TENANT, MQTT_TLS,
                     MQTT_USER, REQUEST_HANDLERS, REQUEST_TO_RESULT,
                     REQ_CAPABILITY, REQ_CAR_MOTOR_SET, REQ_CAR_SERVO_SET,
                     REQ_CAR_SERVO_STATUS, REQ_DISCOVERY, REQ_PHONE_AUDIO_SEND,
                     REQ_PHONE_AUDIO_STOP, REQ_STATE, REQ_VIDEO_START,
                     REQ_VIDEO_STOP, RES_ALARM, RES_CAPABILITY_RESULT,
                     RES_CAR_SERVO_STATUS_RESULT, RES_CAR_MOTOR_SET_RESULT,
                     RES_DISCOVERY_RESULT, RES_ONLINE,
                     RES_PHONE_AUDIO_SEND_RESULT, RES_PHONE_AUDIO_STOP_RESULT,
                     RES_STATE_RESULT, RES_VIDEO_START_RESULT,
                     RES_VIDEO_STOP_RESULT, VIDEO_ENABLE, VIDEO_ROOM_PATTERN,
                     device_topic, device_topic_base, sanitize_segment,
                     subscriptions, tenant_topic, tls_enabled,
                     validate_mqtt_config)
from .identity import (get_cpu_core_count, get_lan_ip,
                       pick_primary_mac, resolve_device_id)
from .media import rtmp_secret_ready  # noqa: F401
from .monitor import MonitorMixin, scan_usb_devices
from .phone_audio import PhoneAudioMixin
from .protocol import ProtocolMixin
from .record import RecordMixin
from .video import VideoMixin


def __getattr__(name):
    """兼容旧的 `agent.DEVICE_ID` 等运行期配置读取。"""
    try:
        return getattr(config, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}") from None


class RpiMonitor(ProtocolMixin, ControlMixin, MonitorMixin, SensorAlarmMixin,
                 CapabilityMixin, VideoMixin, RecordMixin, PhoneAudioMixin,
                 CarMotorMixin, CarServoMixin):
    """设备代理：组合协议、监控、能力、推流、录像、手机音频、车控与舵机状态能力。"""

    def __init__(self):
        self.client = None
        self.stop_event = threading.Event()
        # 查询/动作分池 + 有界排队（P2-1.4）：
        # · 监控采样在 agent 主线程（monitor_loop），与请求池完全解耦；
        # · 慢动作（推流等首帧/宽限期、音频启动）不占用状态/能力查询线程；
        # · BoundedSemaphore 限制「执行中 + 排队」总量，洪峰时丢弃并计数，
        #   避免无界队列吃内存。
        self.query_pool = ThreadPoolExecutor(config.QUERY_WORKERS,
                                             thread_name_prefix="mqtt-query")
        self.action_pool = ThreadPoolExecutor(config.ACTION_WORKERS,
                                              thread_name_prefix="mqtt-action")
        self.servo_pool = ThreadPoolExecutor(config.SERVO_WORKERS,
                                             thread_name_prefix="mqtt-servo")
        self._query_slots = threading.BoundedSemaphore(config.QUERY_WORKERS + config.MAX_QUERY_BACKLOG)
        self._action_slots = threading.BoundedSemaphore(config.ACTION_WORKERS + config.MAX_ACTION_BACKLOG)
        self._servo_slots = threading.BoundedSemaphore(config.SERVO_WORKERS + config.MAX_SERVO_BACKLOG)
        self.dropped_requests = 0
        self._dropped_lock = threading.Lock()
        self._last_drop_log = 0.0
        # psutil 的 CPU 采样在进程内共享上一次时间点，多线程采样需加锁
        self.sample_lock = threading.Lock()
        self.active_alarms = set()
        self.usb_state = scan_usb_devices()
        # 电池低压 / 前方障碍告警状态（V1.6）：设备端只上报，不落盘
        self.init_alarm_sensors()
        self.lan_ip = get_lan_ip()
        self.mac = pick_primary_mac(self.lan_ip)
        # device_id：落盘优先，首次才按硬件指纹（MAC + CPU 核数 + 内存 GiB）生成。
        # 每次重算会因为 MemTotal 浮动而漂移，导致同一台设备换主题、App 出两张卡片。
        self.cpu_cores = get_cpu_core_count()
        self.mem_total = psutil.virtual_memory().total
        config.DEVICE_ID, config.DEVICE_ID_SOURCE = resolve_device_id(self.mac, self.cpu_cores, self.mem_total)
        logging.info("设备 device_id: %s（来源：%s；MAC=%s, CPU核心数=%s, 内存总容量=%s字节）",
                     config.DEVICE_ID, config.DEVICE_ID_SOURCE, self.mac, self.cpu_cores, self.mem_total)
        # 反向音频：手机麦上行后在本地播放的 ffmpeg 进程（与视频互不影响）
        self.audio_proc = None
        self.audio_lock = threading.Lock()
        self.audio_meta = {}
        # App/Web 客户端在线状态：client_id -> {online, client_name, ...}
        self.client_presence = {}
        self.client_presence_lock = threading.Lock()
        # agent 在线状态：agent_id -> {online, agent_name, ...}
        self.agent_presence = {}
        self.agent_presence_lock = threading.Lock()
        # 控制权租约（单 holder，内存态；设备重启清空）
        self.control_lease = ControlLease(
            config.CONTROL_LEASE_TTL_SEC, config.CONTROL_OFFLINE_GRACE_SEC)
        self._last_control_denied_log = 0.0
        # 上一次应答 discovery 的时间（单调时钟），用于限频
        self._last_discovery_at = 0.0
        # 内部录像状态（v1.10 起不再进入 baseState/query/response）
        self.recording_state = {"recording": False, "filename": None}
        # 运行状态：电机是否在动、舵机动作截止时间（由 node.py 维护）
        self.motor_moving = False
        self.servo_moving_until = 0.0
        # baseState/query/request 快照：monitor_loop 每次采样后写入，查询直接复用，
        # 避免每次查询都阻塞采样 1s（P2-1.1）
        self.status_sample = None       # (cores, per_iface, total_rx, total_tx, interval, monotonic_ts)
        self.status_sample_lock = threading.Lock()

    def run(self):
        problems = config.validate_mqtt_config()
        if problems:
            raise RuntimeError(
                "MQTT 配置不完整，拒绝启动：\n"
                + "\n".join(f"  - {p}" for p in problems)
                + "\n请编辑 /etc/walle-ai.conf（参考 scripts/walle-ai.conf.example），"
                  "至少配置 MQTT_HOST / MQTT_USERNAME / MQTT_PASSWORD / MQTT_TENANT；"
                  "生产环境保持 TLS（MQTT_PORT=8883、MQTT_TLS=on）。")
        logging.info("设备 %s (%s) 启动监控，采样周期 %ss", config.DEVICE_ID, self.lan_ip, config.MONITOR_INTERVAL)
        logging.info("协议主题: tenant=%s device=%s broker=%s:%s",
                     config.sanitize_segment(config.MQTT_TENANT), config.sanitize_segment(config.DEVICE_ID),
                     config.MQTT_HOST, MQTT_PORT)
        if config.sanitize_segment(config.MQTT_TENANT) == config.sanitize_segment(config.MQTT_USER):
            logging.warning(
                "租户当前取自 MQTT 用户名（%s）。如果手机 App 用的是另一个账号登录，"
                "请在 /etc/walle-ai.conf 里设置 MQTT_TENANT=<App 的登录用户名>，"
                "否则 App 订阅 walle/<App用户名>/# 时收不到本设备的报文。",
                config.sanitize_segment(config.MQTT_TENANT))
        # P2-1.1：连接 broker 前先填充一次状态快照，保证上线后的首个 baseState/query/request 也能立即响应
        try:
            sample = self.sample_once(0.2)
            self._update_status_sample(*sample)
        except Exception:
            logging.exception("启动采样失败，baseState/query/request 将退化为现场采样")
        # clean_session=True：每次连接都重新订阅，改了请求主题名也不会残留旧订阅
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=config.DEVICE_ID, clean_session=True)
        self.client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)
        # 遗嘱：异常掉线时 broker 代发 offline 到 walle/{tenant}/device/{device}/online
        # （遗嘱必须在 connect 前设置，因此里面的静态信息取自启动时刻）
        will_topic, will_payload = self._will_message()
        self.client.will_set(
            will_topic,
            json.dumps(will_payload, ensure_ascii=False),
            qos=config.MQTT_QOS, retain=True,
        )
        logging.info("遗嘱(LWT)主题: %s", will_topic)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        # paho 内部错误（回调异常、连接异常）默认只写它自己的 logger，接进统一日志
        self.client.enable_logger()
        self.client.reconnect_delay_set(min_delay=3, max_delay=30)
        if config.tls_enabled():
            import ssl
            try:
                ctx = ssl.create_default_context(cafile=config.MQTT_CA_FILE or None)
            except (OSError, ssl.SSLError) as exc:
                raise RuntimeError(
                    f"加载 CA 失败（MQTT_CA_FILE={config.MQTT_CA_FILE or 'system'}）：{exc}") from exc
            self.client.tls_set_context(ctx)
            logging.info("启用 TLS 连接 %s:%s（CA=%s）",
                         config.MQTT_HOST, MQTT_PORT, config.MQTT_CA_FILE or "system")
        else:
            logging.warning("调试模式：使用明文 MQTT 连接 %s:%s（WALLE_RUNTIME_ENV=%s）",
                            config.MQTT_HOST, MQTT_PORT, config.WALLE_RUNTIME_ENV)
        self.client.connect(config.MQTT_HOST, MQTT_PORT, config.MQTT_KEEPALIVE)
        self.client.loop_start()

        try:
            self.monitor_loop()
        finally:
            logging.info("正在退出，停止推流、发送 offline 并断开连接...")
            self._stop_video_stream("程序退出")
            self._stop_phone_audio("程序退出")
            try:
                # 双发：LWT 只挂了一个主题，正常退出时把另一套也置为 offline
                self.publish_json(config.RES_ONLINE, self.build_online_payload(False), retain=True)
                time.sleep(0.5)
            except Exception:
                pass
            self.query_pool.shutdown(wait=False, cancel_futures=True)
            self.action_pool.shutdown(wait=False, cancel_futures=True)
            self.servo_pool.shutdown(wait=False, cancel_futures=True)
            self.client.loop_stop()
            self.client.disconnect()


def print_config_check():
    """--check：打印解析后的配置与主题，不连 broker、不做采集。"""
    try:
        mac = pick_primary_mac(get_lan_ip())
        # persist=False：只看会用什么标识，不创建/修改文件
        config.DEVICE_ID, config.DEVICE_ID_SOURCE = resolve_device_id(
            mac, get_cpu_core_count(), psutil.virtual_memory().total, persist=False)
    except Exception as exc:                      # 取不到网卡/内存信息时不影响看配置
        config.DEVICE_ID = f"<无法计算: {exc}>"
        config.DEVICE_ID_SOURCE = "计算失败"
    problems = config.validate_mqtt_config()
    tls_on = config.tls_enabled()
    print("== 生效配置 ==")
    print(f"  环境文件 : {config.ENV_SOURCE or '未加载（使用环境变量）'}")
    print(f"  配置校验 : {'✅ 通过' if not problems else '❌ 未通过'}")
    for problem in problems:
        print(f"    - {problem}")
    print(f"  Broker   : {config.MQTT_HOST or '(未配置)'}:{MQTT_PORT}   "
          f"TLS={'on' if tls_on else 'off'} (MQTT_TLS={config.MQTT_TLS})")
    print(f"  用户名   : {config.MQTT_USER}")
    print(f"  密码     : {'********' if config.MQTT_PASSWORD else '(空)'}")
    print(f"  媒体服务器: {config.MEDIA_SERVER_HOST or '(未配置)'}")
    print(f"  推流 secret: {'已配置' if config.MEDIA_SERVER_SECRET else '未配置（视频推流会被服务端拒绝）'}")
    print(f"  租户     : {config.sanitize_segment(config.MQTT_TENANT)}")
    print(f"  设备号   : {config.DEVICE_ID}")
    if config.DEVICE_ID_SOURCE:
        print(f"  标识来源 : {config.DEVICE_ID_SOURCE}")
    if config.DEVICE_ID.startswith("<"):
        print("  订阅主题 : （设备号计算失败，跳过拼接；在树莓派上运行可正常计算）")
    else:
        print(f"  主题前缀 : walle/{config.sanitize_segment(config.MQTT_TENANT)}/device/{config.DEVICE_ID}/")
        print("  订阅主题 :")
        for topic, _qos in config.subscriptions():
            print(f"    - {topic}")
    if config.sanitize_segment(config.MQTT_TENANT) == config.sanitize_segment(config.MQTT_USER):
        print(f"  提示     : 租户与用户名相同（{config.MQTT_TENANT}）。若手机 App 用别的账号登录，"
              f"请把 MQTT_TENANT 改成 App 的用户名")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=os.environ.get(
            "WALLE_PY_LOG_FORMAT",
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s"),
        datefmt=os.environ.get("WALLE_PY_LOG_DATEFMT", "%Y-%m-%d %H:%M:%S"),
    )
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return 0
    if "--check" in sys.argv:
        print_config_check()
        return 0

    logging.info("配置来源: %s", config.ENV_SOURCE or "环境变量")
    monitor = RpiMonitor()

    def _shutdown(signum, frame):
        logging.info("收到信号 %s，准备停止...", signum)
        monitor.stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
