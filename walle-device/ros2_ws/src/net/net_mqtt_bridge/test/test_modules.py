#!/usr/bin/env python3
"""P2-2.1：agent.py 拆分后的模块边界与文件大小约束。"""

import importlib
from pathlib import Path

import net_mqtt_bridge

MODULES = (
    "config", "identity", "monitor", "protocol", "capability",
    "media", "audio", "video", "phone_audio", "car_motor", "car_servo", "agent",
)


def test_modules_import_independently():
    for name in MODULES:
        module = importlib.import_module("net_mqtt_bridge." + name)
        assert module is not None


def test_module_files_stay_under_500_lines():
    package_dir = Path(net_mqtt_bridge.__file__).parent
    for path in sorted(package_dir.glob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines < 500, f"{path.name}: {lines} 行，超过 500 行上限"
