#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RaspberryPi 系统监控 Agent（基于 paho-mqtt 2.x）

功能：
1. 周期巡检，异常实时上报（USB 插拔 / CPU / 内存 / 磁盘 / 带宽 / 温度），带状态机：
   越限才报，恢复后发 recover 事件，不会重复刷屏。
2. 响应设备身份查询、状态查询、摄像头能力查询、推流/停止推流。
3. 上线/下线：retained + 遗嘱（LWT），App 重启或重连后能立刻看到设备在线状态。

协议：多租户主题 v1
    所有主题都在 walle/{tenant}/{device}/v1/ 下展开，租户 = MQTT 用户名（清洗后），
    device 用稳定的 device_id：首次启动按「MAC + CPU 核心数 + 内存 GiB」算 md5 前 16 位，
    之后落盘复用（resolve_device_id），不会因为 MemTotal 的 KiB 级浮动而漂移。

    请求（App -> 设备）                          应答（设备 -> App）                      说明
    ------------------------------------------------------------------------------------------------
    —                                          .../online                          主动上报（retained + LWT）
    —                                          .../alarm                           主动上报（告警 / USB）
    walle/{t}/discovery/query                   .../discovery/result                 设备发现（租户级广播）
    .../state/query                             .../state/result                     状态快照
    .../capability/query                      .../capability/result                  设备能力（摄像头/麦克风/音响等外设）
    .../video/start/request                     .../video/start/result               开始推流（动作）
    .../video/stop/request                      .../video/stop/result                停止推流（动作）
    .../phone/audio/send                        .../phone/audio/send/result            手机麦上行·开（App 发起）
    .../phone/audio/stop                        .../phone/audio/stop/result            手机麦上行·关（App 发起）

    命名约定：请求按语义划分 —— 查询 /query、动作 /request；
    应答一律 /result，且只在收到对应请求后才发布，只有 online 与 alarm 是主动上报。

    除 discovery 广播外，设备只订阅自己子树下的「请求类」主题，
    不订通配子树（避免收到自己发布的上报报文）。

安全：所有请求报文必须带匹配的 uuid（本次运行实例标识），否则静默忽略；
      设备连接用独立 MQTT 账号，Broker 侧按 walle/${clientid}/# 授权。

依赖：pip install -r requirements.txt  （paho-mqtt>=2.0、psutil）
     摄像头能力探测需 v4l-utils，推流需 ffmpeg。

配置：全部通过环境变量；前台运行时会自动加载 /etc/walle-ai.conf，
      查找顺序 --env-file > $RPI_MONITOR_ENV > /etc/walle-ai.conf > ./walle-ai.conf，
      已存在的 shell 变量优先。必填项见 README「首次部署」。

命令行：
    python3 main.py                         前台运行（自动读 /etc/walle-ai.conf）
    python3 main.py --check                 只打印解析后的配置与订阅主题，不连 broker
    python3 main.py --env-file PATH         使用指定配置文件
    python3 main.py --help                  查看本说明

部署：见 linux-demo/README.md「首次部署」（systemd 单元为 walle-ai.service）
"""

import logging
import os
import signal
import sys

import psutil

import ld_config as config
from ld_config import *          # noqa: F401,F403
from ld_protocol import *        # noqa: F401,F403
from ld_utils import *           # noqa: F401,F403
from ld_camera import *          # noqa: F401,F403
from ld_audio import *           # noqa: F401,F403
from ld_media import *           # noqa: F401,F403
from ld_monitor import RpiMonitor


def print_config_check():
    """--check：打印解析后的配置与主题，不连 broker、不做采集；返回配置错误列表。"""
    try:
        mac = pick_primary_mac(get_lan_ip())
        # persist=False：只看会用什么标识，不创建/修改文件
        config.DEVICE_ID, config.DEVICE_ID_SOURCE = resolve_device_id(
            mac, get_cpu_core_count(), psutil.virtual_memory().total, persist=False)
    except Exception as exc:                      # 取不到网卡/内存信息时不影响看配置
        config.DEVICE_ID = f"<无法计算: {exc}>"
        config.DEVICE_ID_SOURCE = "计算失败"
    errors = validate_config()
    tls_on = MQTT_TLS == "on"
    host = MQTT_HOST or "(未配置)"
    port = MQTT_PORT if MQTT_PORT is not None else "(未配置)"
    tenant = sanitize_segment(config.MQTT_TENANT) if config.MQTT_TENANT else "(未配置)"
    print("== 生效配置 ==")
    print(f"  环境文件 : {config.ENV_SOURCE or '未加载（使用环境变量）'}")
    print(f"  运行环境 : {WALLE_RUNTIME_ENV}")
    print(f"  版本     : {config.VERSION_NAME} ({config.VERSION_CODE})，"
          f"来源: {config.VERSION_SOURCE}")
    print(f"  Broker   : {host}:{port}   TLS={'on' if tls_on else 'off'} (MQTT_TLS={MQTT_TLS})")
    print(f"  用户名   : {MQTT_USER or '(未配置)'}")
    print(f"  密码     : {'********' if MQTT_PASSWORD else '(未配置)'}")
    print(f"  Oryx secret: {'已配置' if WHIP_SECRET else '未配置（视频推流会被服务端拒绝）'}")
    print(f"  推流模板 : {'已配置' if VIDEO_RTMP_URL_TEMPLATE else '未配置（视频推流不可用）'}")
    print(f"  手机音频 : {'已配置' if PHONE_AUDIO_WHEP_URL_TEMPLATE else '未配置（手机音频不可用）'}")
    print(f"  摄像头   : {CAMERA_DEVICE or '任意 /dev/videoN（推流前 preflight）'}")
    print(f"  租户     : {tenant}")
    print(f"  设备号   : {config.DEVICE_ID}")
    if config.DEVICE_ID_SOURCE:
        print(f"  标识来源 : {config.DEVICE_ID_SOURCE}")
    if not config.MQTT_TENANT:
        print("  订阅主题 : （MQTT_TENANT 未配置，跳过拼接）")
    elif config.DEVICE_ID.startswith("<"):
        print("  订阅主题 : （设备号计算失败，跳过拼接；在树莓派上运行可正常计算）")
    else:
        print(f"  主题前缀 : walle/{tenant}/{config.DEVICE_ID}/")
        print("  订阅主题 :")
        for topic, _qos in subscriptions():
            print(f"    - {topic}")
    if config.MQTT_TENANT and MQTT_USER and tenant == sanitize_segment(MQTT_USER):
        print(f"  提示     : 租户与用户名相同（{config.MQTT_TENANT}）。"
              f"若手机 App 用别的账号登录，请把 MQTT_TENANT 改成 App 的用户名")
    if errors:
        print("\n== 配置检查失败 ==")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\n== 配置检查通过 ==")
    warnings = [w for w in media_config_warnings()
                if not any(err in w for err in errors)]
    if config.VERSION_SOURCE.startswith("内置默认值"):
        warnings.append("未找到 VERSION 文件，使用内置默认版本；"
                        "单独部署时请把 walle-device/VERSION 放到 main.py 同级")
    if warnings:
        print("\n== 配置警告 ==")
        for warn in warnings:
            print(f"  - {warn}")
    print("\n== 依赖检查 ==")
    for item in dependency_status():
        state = item["path"] if item["ok"] else f"未找到（{item['impact']}）"
        print(f"  {item['name']}: {state}")
    return errors


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.info("版本: %s (%s)，来源: %s",
                 config.VERSION_NAME, config.VERSION_CODE, config.VERSION_SOURCE)
    if config.VERSION_SOURCE.startswith("内置默认值"):
        logging.warning("未找到 VERSION 文件，使用内置默认版本；"
                        "单独部署时请把 walle-device/VERSION 放到 main.py 同级")
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return 0
    if "--check" in sys.argv:
        return 1 if print_config_check() else 0

    errors = validate_config()
    if errors:
        for err in errors:
            logging.error("配置错误: %s", err)
        logging.error("拒绝启动：请通过环境变量或环境文件补齐上述必填项")
        return 2

    for warning in media_config_warnings():
        logging.warning("配置警告: %s", warning)

    logging.info("配置来源: %s", config.ENV_SOURCE or "环境变量")
    monitor = RpiMonitor()

    def _shutdown(signum, frame):
        logging.info("收到信号 %s，准备停止...", signum)
        monitor.stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    monitor.run()
    # 线程池任务可能阻塞在 v4l2/pactl；清理完成后直接退出，
    # 避免解释器等待非 daemon 线程导致 systemd stop 卡到 TimeoutStopSec。
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
