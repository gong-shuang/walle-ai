#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RpiMonitor 组合：按职责拆分的 mixin + 基础设施。"""
from ld_monitor_base import RpiMonitorBase
from ld_monitor_identity import MonitorIdentityMixin
from ld_monitor_capability import MonitorCapabilityMixin
from ld_monitor_video import MonitorVideoMixin
from ld_monitor_phone import MonitorPhoneMixin
from ld_monitor_status import MonitorStatusMixin


class RpiMonitor(MonitorStatusMixin, MonitorIdentityMixin, MonitorCapabilityMixin,
                 MonitorVideoMixin, MonitorPhoneMixin, RpiMonitorBase):
    """设备端主对象：MQTT 接入、监控、能力探测、推流与手机音频。"""
