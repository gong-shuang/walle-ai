# -*- coding: utf-8 -*-
"""M4-02：LLM 工具 schema 与参数校验。

模型只允许输出本文件声明的工具调用（`action` + 参数），不得生成 MQTT
topic / payload / device；设备由 chat 请求上下文提供。

`validate_intent()` 同时接受两种形状：

```json
{"action": "move", "direction": "forward", "distance_m": 3}
{"name": "move", "arguments": {"direction": "forward", "distance_m": 3}}
```
"""

import json
import re


class SchemaError(ValueError):
    """工具名或参数不符合 schema。"""


def _object_schema(properties=None, required=None, **extra):
    schema = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    schema.update(extra)
    return schema


_SERVO_IDS = {"type": "integer", "minimum": 1, "maximum": 4}
_DURATION = {"type": "number", "minimum": 0.1, "maximum": 5.0}
_TASK_TARGET = _object_schema(
    {
        "task_id": {"type": "string", "pattern": r"^task-[0-9a-fA-F]{8}$"},
        "task_name": {"type": "string", "minLength": 1},
    },
    anyOf=[{"required": ["task_id"]}, {"required": ["task_name"]}],
)
_RECORD_TARGET = _object_schema(
    {
        "filename": {"type": "string", "minLength": 1},
        "latest": {"type": "boolean"},
    },
    anyOf=[{"required": ["filename"]}, {"required": ["latest"]}],
)


TOOLS = {
    "move": {
        "description": "让小车前进、后退、左移或右移指定距离",
        "parameters": _object_schema({
            "direction": {"type": "string",
                          "enum": ["forward", "backward", "left", "right"]},
            "distance_m": {"type": "number", "exclusiveMinimum": 0},
            "speed_mps": {"type": "number", "minimum": 0},
        }, ["direction", "distance_m"]),
    },
    "rotate": {
        "description": "原地左转或右转指定角度",
        "parameters": _object_schema({
            "direction": {"type": "string", "enum": ["left", "right"]},
            "angle_deg": {"type": "number", "exclusiveMinimum": 0},
        }, ["direction", "angle_deg"]),
    },
    "stop": {
        "description": "立即停止运动并释放控制权",
        "parameters": _object_schema(),
    },
    "status": {
        "description": "查询设备基础状态与运行状态",
        "parameters": _object_schema(),
    },
    "device_base_state": {
        "description": "查询设备 CPU、内存、磁盘、网络、USB 等基础状态",
        "parameters": _object_schema(),
    },
    "device_run_state": {
        "description": "查询录像、推流、电量、声呐、控制权等运行状态",
        "parameters": _object_schema(),
    },
    "device_discover": {
        "description": "发现当前租户下的设备",
        "parameters": _object_schema(),
    },
    "agent_status": {
        "description": "查询 agent 所在机器的运行状态",
        "parameters": _object_schema(),
    },
    "service_health": {
        "description": "检查 agent、MQTT、设备等服务的健康状态",
        "parameters": _object_schema(),
    },
    "alarm_status": {
        "description": "查询最近一条告警",
        "parameters": _object_schema(),
    },
    "record_start": {
        "description": "开始录像；segment_sec 可指定分段时长，0 表示设备默认",
        "parameters": _object_schema({
            "segment_sec": {"type": "integer", "minimum": 0, "maximum": 86400},
        }),
    },
    "record_stop": {
        "description": "停止录像",
        "parameters": _object_schema(),
    },
    "record_list": {
        "description": "查看录像列表",
        "parameters": _object_schema(),
    },
    "record_delete": {
        "description": "删除指定录像文件或最新录像（待 M5 实现）",
        "parameters": _RECORD_TARGET,
    },
    "record_play_start": {
        "description": "播放指定录像或最新录像（待 M5 实现）",
        "parameters": _RECORD_TARGET,
    },
    "record_play_stop": {
        "description": "停止播放录像（待 M5 实现）",
        "parameters": _object_schema(),
    },
    "stream_start": {
        "description": "开始推流",
        "parameters": _object_schema(),
    },
    "stream_stop": {
        "description": "停止推流",
        "parameters": _object_schema(),
    },
    "servo_status": {
        "description": "查询舵机当前位置与可用状态",
        "parameters": _object_schema(),
    },
    "servo_set": {
        "description": "把 1-4 号舵机设置到指定位置",
        "parameters": _object_schema({
            "servos": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": _object_schema({
                    "id": _SERVO_IDS,
                    "position": {"type": "integer",
                                 "minimum": 800, "maximum": 2200},
                }, ["id", "position"]),
            },
            "duration": _DURATION,
        }, ["servos"]),
    },
    "servo_center": {
        "description": "舵机回中",
        "parameters": _object_schema(),
    },
    "servo_relative": {
        "description": "在当前舵机位置上相对调整 delta（正数向上/向右）",
        "parameters": _object_schema({
            "servo_id": _SERVO_IDS,
            "delta": {"type": "integer", "minimum": -1400,
                      "maximum": 1400},
            "duration": _DURATION,
        }, ["servo_id", "delta"]),
    },
    "task_list": {
        "description": "查看 agent 上的定时任务",
        "parameters": _object_schema(),
    },
    "task_create": {
        "description": "创建定时录像任务",
        "parameters": _object_schema({
            "name": {"type": "string", "minLength": 1, "maxLength": 64},
            "timezone": {"type": "string"},
            "schedule": {
                "type": "object",
                "description": (
                    "daily_window:{type,start,stop,weekdays?}；"
                    "once:{type,at}；cron:{type,expr}；"
                    "interval:{type,seconds}"),
                "properties": {
                    "type": {"type": "string",
                             "enum": ["daily_window", "once", "cron",
                                      "interval"]},
                    "start": {"type": "string",
                              "pattern": r"^\d{2}:\d{2}(:\d{2})?$"},
                    "stop": {"type": "string",
                             "pattern": r"^\d{2}:\d{2}(:\d{2})?$"},
                    "weekdays": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "integer", "minimum": 0,
                                  "maximum": 6},
                    },
                    "at": {"type": "string"},
                    "expr": {"type": "string"},
                    "seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["type"],
                "additionalProperties": False,
            },
            "actions": {
                "type": "array",
                "minItems": 1,
                "items": _object_schema({
                    "phase": {"type": "string", "enum": ["start", "stop"]},
                    "tool": {"type": "string",
                             "enum": ["record.start", "record.stop"]},
                }, ["phase", "tool"]),
            },
        }, ["schedule", "actions"]),
    },
    "task_cancel": {
        "description": "取消任务；task_id 或 task_name 二选一",
        "parameters": _TASK_TARGET,
    },
    "task_pause": {
        "description": "暂停任务；task_id 或 task_name 二选一",
        "parameters": _TASK_TARGET,
    },
    "task_resume": {
        "description": "恢复任务；task_id 或 task_name 二选一",
        "parameters": _TASK_TARGET,
    },
}


def tool_schemas():
    """OpenAI tools 参数格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for name, tool in TOOLS.items()
    ]


def system_prompt():
    """给 JSON-mode 模型的系统提示；只列出工具与参数，不暴露 MQTT。"""
    lines = [
        "你是小车控制意图解析器。只输出一个 JSON 对象，不要解释。",
        "只能调用下列工具；device 由请求上下文提供，禁止输出 device、topic、payload。",
    ]
    for name, tool in TOOLS.items():
        params = tool["parameters"].get("properties") or {}
        required = set(tool["parameters"].get("required") or [])
        hints = []
        for key, spec in params.items():
            text = key
            if spec.get("type"):
                text += ":" + str(spec["type"])
            if spec.get("enum"):
                text += "(" + "/".join(str(item) for item in spec["enum"]) + ")"
            if key not in required:
                text += "?"
            hints.append(text)
        lines.append("- {}: {}；参数：{}".format(
            name, tool["description"], ", ".join(hints) or "无"))
    lines.append('输出示例：{"action":"move","direction":"forward","distance_m":3}')
    lines.append(
        '任务示例：{"action":"task_create","name":"每天8点录像",'
        '"schedule":{"type":"daily_window","start":"08:00","stop":"21:00"},'
        '"actions":[{"phase":"start","tool":"record.start"},'
        '{"phase":"stop","tool":"record.stop"}]}')
    lines.append('无法确定时输出 {"action":"unknown"}。')
    return "\n".join(lines)


def _type_ok(value, expected):
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _check(value, schema, path):
    if not isinstance(schema, dict):
        return
    if "anyOf" in schema:
        errors = []
        for candidate in schema["anyOf"]:
            try:
                _check(value, candidate, path)
                break
            except SchemaError as exc:
                errors.append(str(exc))
        else:
            raise SchemaError("{}必须满足：{}".format(
                path + " " if path else "", "；".join(errors)))
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise SchemaError("{}必须是 {}".format(
            path or "参数", "/".join(str(item) for item in enum)))
    expected = schema.get("type")
    if expected and not _type_ok(value, expected):
        raise SchemaError("{}类型必须是 {}".format(path or "参数", expected))
    if expected == "object" or (
            expected is None
            and any(key in schema for key in ("properties", "required",
                                              "additionalProperties"))):
        if not isinstance(value, dict):
            raise SchemaError("{}类型必须是 object".format(path or "参数"))
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            extra = [key for key in value if key not in properties]
            if extra:
                raise SchemaError("不支持参数 {}".format(
                    "、".join(sorted(extra))))
        for key in schema.get("required") or []:
            if key not in value:
                raise SchemaError("缺少参数 {}".format(key))
        for key, child in properties.items():
            if key in value:
                _check(value[key], child,
                       "{}.{}".format(path, key) if path else key)
        return
    if expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError("{}至少 {} 项".format(path or "参数",
                                                  schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError("{}最多 {} 项".format(path or "参数",
                                                  schema["maxItems"]))
        items = schema.get("items") or {}
        for index, item in enumerate(value):
            _check(item, items, "{}[{}]".format(path or "参数", index))
        return
    if expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError("{}长度不足".format(path or "参数"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError("{}长度超限".format(path or "参数"))
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            raise SchemaError("{}格式不正确".format(path or "参数"))
        return
    if expected in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError("{}不能小于 {}".format(path or "参数",
                                                    schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError("{}不能大于 {}".format(path or "参数",
                                                    schema["maximum"]))
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise SchemaError("{}必须大于 {}".format(
                path or "参数", schema["exclusiveMinimum"]))
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise SchemaError("{}必须小于 {}".format(
                path or "参数", schema["exclusiveMaximum"]))


def validate_tool_call(name, args=None):
    """校验工具名与参数，返回内部 intent；失败抛 SchemaError。"""
    name = str(name or "").strip()
    tool = TOOLS.get(name)
    if tool is None:
        raise SchemaError("不支持的工具 {}".format(name or "（空）"))
    args = {} if args is None else args
    if not isinstance(args, dict):
        raise SchemaError("工具参数必须是对象")
    _check(args, tool["parameters"], "")
    intent = {"action": name}
    intent.update(args)
    return intent


def _unwrap(payload):
    """兼容 OpenAI function call 形状。"""
    if "function" in payload and isinstance(payload["function"], dict):
        payload = payload["function"]
    if "name" in payload and "action" not in payload:
        args = payload.get("arguments", payload.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError as exc:
                raise SchemaError("工具参数不是合法 JSON") from exc
        if not isinstance(args, dict):
            raise SchemaError("工具参数必须是对象")
        return payload["name"], args
    action = payload.get("action")
    args = {key: value for key, value in payload.items() if key != "action"}
    return action, args


def validate_intent(payload):
    """校验模型返回的 intent，返回 (intent, error)。

    校验失败时 intent 为 None，error 为可读原因，调用方可回退规则或反问。
    """
    if not isinstance(payload, dict):
        return None, "模型输出必须是 JSON 对象"
    if "topic" in payload or "payload" in payload:
        return None, "模型只能输出工具调用，不能生成 topic/payload"
    try:
        name, args = _unwrap(payload)
        return validate_tool_call(name, args), None
    except SchemaError as exc:
        return None, str(exc)
