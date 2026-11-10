#!/usr/bin/env python3
"""P1-5.1：远程命令执行能力必须保持移除状态。

任何重新引入 `cmd/exec` 主题、`handle_cmd_exec` 处理函数或
`remote_cmd` 能力声明的改动，都应让本测试失败。
"""

from pathlib import Path

import net_mqtt_bridge
import net_mqtt_bridge.agent as agent


def test_no_cmd_exec_subscription():
    topics = [topic for topic, _ in agent.subscriptions()]
    assert all("cmd/exec" not in topic for topic in topics), topics


def test_no_cmd_exec_handler():
    assert "handle_cmd_exec" not in agent.REQUEST_HANDLERS.values()


def test_no_remote_cmd_capability():
    package_dir = Path(net_mqtt_bridge.__file__).parent
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py")))
    assert "remote_cmd" not in source
    assert "cmd/exec" not in source
    assert "handle_cmd_exec" not in source
