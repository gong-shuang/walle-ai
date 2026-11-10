#!/usr/bin/env python3
"""P2-2.3：设备版本号单一来源（ROS2 工作空间 walle_ws/VERSION）。"""

import os
from pathlib import Path

import net_mqtt_bridge.config as config


def test_parse_version_file(tmp_path):
    path = tmp_path / "VERSION"
    path.write_text(
        "export WALLE_VERSION_NUM=7\n"
        "export WALLE_VERSION_NAME=1.2.3\n"
        "export WALLE_VERSION_DATE=2026-11-10\n",
        encoding="utf-8")
    values = config._parse_version_file(str(path))
    assert values["WALLE_VERSION_NUM"] == "7"
    assert values["WALLE_VERSION_NAME"] == "1.2.3"
    assert values["WALLE_VERSION_DATE"] == "2026-11-10"


def test_version_file_under_workspace():
    assert config.VERSION_FILE.endswith("VERSION")
    assert Path(config.VERSION_FILE).parent.name == "walle_ws"


def test_workspace_dir_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLE_HOME", str(tmp_path))
    monkeypatch.delenv("WALLE_WS", raising=False)
    assert config._workspace_dir() == tmp_path / "walle_ws"


def test_version_constants_come_from_version_file():
    if not os.path.isfile(config.VERSION_FILE):
        # 非源码部署且未注入环境变量时使用兜底值
        assert config.VERSION_NAME == os.environ.get("WALLE_VERSION_NAME", "0.0.0")
        return
    values = config._parse_version_file(config.VERSION_FILE)
    expected_name = os.environ.get("WALLE_VERSION_NAME") or values["WALLE_VERSION_NAME"]
    expected_code = os.environ.get("WALLE_VERSION_NUM") or values["WALLE_VERSION_NUM"]
    assert config.VERSION_NAME == expected_name
    assert config.VERSION_CODE == int(expected_code)
