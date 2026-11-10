#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本号读取优先级测试：环境变量 > VERSION 文件 > 内置默认值。

运行（需已安装 requirements.txt）：
    cd walle-device/linux-demo
    python3 -m unittest test_version -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN_PY = HERE / "main.py"
VERSION_KEYS = (
    "WALLE_VERSION_NUM",
    "WALLE_VERSION_NAME",
    "WALLE_VERSION_FILE",
    "WALLE_VERSION_DATE",
)
# 打印 "版本号 版本名 来源"，避免与 --check 的其他输出混在一起
PROBE = "import main; print(main.VERSION_CODE, main.VERSION_NAME, main.VERSION_SOURCE)"


def _env(**extra):
    """构造干净环境：去掉宿主的版本变量，并给一份能通过 MQTT 校验的假配置。"""
    env = {k: v for k, v in os.environ.items() if k not in VERSION_KEYS}
    env.update({
        "WALLE_RUNTIME_ENV": "debug",
        "MQTT_HOST": "broker.test",
        "MQTT_USERNAME": "user",
        "MQTT_PASSWORD": "pass",
        "MQTT_TENANT": "tenant",
    })
    env.update(extra)
    return env


def _probe(cwd, **extra):
    out = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(cwd), env=_env(**extra),
        capture_output=True, text=True, check=True,
    )
    code, name, source = out.stdout.strip().split(" ", 2)
    return int(code), name, source


class VersionPriorityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        shutil.copy2(MAIN_PY, self.dir / "main.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_next_to_main(self):
        (self.dir / "VERSION").write_text(
            "export WALLE_VERSION_NUM=7\nexport WALLE_VERSION_NAME=1.2.3\n",
            encoding="utf-8")
        code, name, source = _probe(self.dir)
        self.assertEqual((code, name), (7, "1.2.3"))
        self.assertEqual(source, f"文件 {self.dir / 'VERSION'}")

    def test_env_overrides_file(self):
        (self.dir / "VERSION").write_text(
            "export WALLE_VERSION_NUM=7\nexport WALLE_VERSION_NAME=1.2.3\n",
            encoding="utf-8")
        code, name, source = _probe(
            self.dir, WALLE_VERSION_NUM="42", WALLE_VERSION_NAME="9.9.9")
        self.assertEqual((code, name), (42, "9.9.9"))
        self.assertEqual(source, "环境变量")

    def test_explicit_version_file(self):
        version = self.dir / "custom.conf"
        version.write_text(
            "export WALLE_VERSION_NUM=8\nexport WALLE_VERSION_NAME=2.0.0\n",
            encoding="utf-8")
        code, name, source = _probe(self.dir, WALLE_VERSION_FILE=str(version))
        self.assertEqual((code, name), (8, "2.0.0"))
        self.assertEqual(source, f"文件 {version}")

    def test_default_when_missing(self):
        code, name, source = _probe(self.dir)
        self.assertEqual((code, name), (1, "0.1.1"))
        self.assertIn("内置默认值", source)


if __name__ == "__main__":
    unittest.main()
