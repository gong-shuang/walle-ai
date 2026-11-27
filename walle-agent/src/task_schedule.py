# -*- coding: utf-8 -*-
"""M3-03：任务类型与下次触发时间计算。

支持的 `schedule` 结构：

```json
{"type": "daily_window", "start": "08:00", "stop": "21:00"}
{"type": "once", "at": "2026-11-28 08:00:00"}
{"type": "cron", "expr": "0 8 * * 1-5"}
{"type": "interval", "seconds": 7200}
```

`next_occurrence(task, now)` 返回 `{"at": "YYYY-MM-DD HH:MM:SS", "phase": "start"}`，
无后续触发时返回 `None`。`advance_task(store, task, now)` 会把结果写回 `next_run_at`。
"""

from datetime import datetime, time as dtime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

from task_store import DEFAULT_TIMEZONE

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SCHEDULE_TYPES = ("daily_window", "once", "cron", "interval")

_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _zone(name):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(str(name or DEFAULT_TIMEZONE))
    except Exception:  # noqa: BLE001 - 非法时区回落到默认
        try:
            return ZoneInfo(DEFAULT_TIMEZONE)
        except Exception:  # noqa: BLE001
            return None


def _parse_clock(text):
    parts = str(text or "").strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError("时间格式应为 HH:MM 或 HH:MM:SS")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError("时间格式应为 HH:MM 或 HH:MM:SS") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError("时间超出范围")
    return dtime(hour, minute, second)


def _parse_at(text, zone):
    text = str(text or "").strip().replace("T", " ")
    for fmt in (TIME_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=zone)
        except ValueError:
            continue
    raise ValueError("时间格式应为 YYYY-MM-DD HH:MM:SS")


def _parse_cron_field(text, lo, hi):
    text = str(text or "").strip()
    if not text:
        raise ValueError("cron 字段不能为空")
    values = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError("cron 字段不能为空")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ValueError("cron 步长非法") from exc
            if step <= 0:
                raise ValueError("cron 步长必须大于 0")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            left, right = part.split("-", 1)
            try:
                start, end = int(left), int(right)
            except ValueError as exc:
                raise ValueError("cron 范围非法") from exc
        else:
            try:
                start = int(part)
            except ValueError as exc:
                raise ValueError("cron 数值非法") from exc
            end = hi if step > 1 else start
        if start < lo or end > hi or start > end:
            raise ValueError("cron 字段超出范围")
        values.update(range(start, end + 1, step))
    return values


def _parse_cron(expr):
    parts = str(expr or "").strip().split()
    if len(parts) != 5:
        raise ValueError("cron 表达式需要 5 段：分 时 日 月 周")
    fields = [_parse_cron_field(part, lo, hi)
              for part, (lo, hi) in zip(parts, _CRON_RANGES)]
    weekdays = {((value - 1) % 7) for value in fields[4]}
    return {
        "minutes": fields[0],
        "hours": fields[1],
        "days": fields[2],
        "months": fields[3],
        "weekdays": weekdays,
        "dom_restricted": parts[2] != "*",
        "dow_restricted": parts[4] != "*",
    }


def validate_schedule(schedule):
    """校验并返回规范化 schedule；非法时抛 ValueError。"""
    if not isinstance(schedule, dict):
        raise ValueError("schedule 必须是对象")
    kind = str(schedule.get("type") or "").strip().lower()
    if kind not in SCHEDULE_TYPES:
        raise ValueError("schedule.type 必须是 {}".format(
            "/".join(SCHEDULE_TYPES)))
    out = dict(schedule)
    out["type"] = kind
    if kind == "daily_window":
        start = _parse_clock(schedule.get("start"))
        stop = _parse_clock(schedule.get("stop"))
        if start == stop:
            raise ValueError("daily_window 的 start 和 stop 不能相同")
        out["start"] = str(schedule.get("start")).strip()
        out["stop"] = str(schedule.get("stop")).strip()
        weekdays = schedule.get("weekdays")
        if weekdays is not None:
            if not isinstance(weekdays, (list, tuple)) or not weekdays:
                raise ValueError("daily_window.weekdays 必须是非空数组")
            clean = sorted({int(day) for day in weekdays})
            if any(day < 0 or day > 6 for day in clean):
                raise ValueError("daily_window.weekdays 取值 0-6（周一=0）")
            out["weekdays"] = clean
    elif kind == "once":
        _parse_at(schedule.get("at"), None)
        out["at"] = str(schedule.get("at")).strip()
    elif kind == "cron":
        _parse_cron(schedule.get("expr"))
        out["expr"] = str(schedule.get("expr")).strip()
    else:
        try:
            seconds = int(schedule.get("seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError("interval.seconds 必须是正整数") from exc
        if seconds <= 0:
            raise ValueError("interval.seconds 必须大于 0")
        out["seconds"] = seconds
    return out


def _to_zone(now, zone):
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def now_in_timezone(timezone=None, now=None):
    """返回指定时区的当前时间（测试可注入 now）。"""
    return _to_zone(now, _zone(timezone))


def _phase_for(task, schedule):
    phase = str(schedule.get("phase") or "").strip()
    if phase:
        return phase
    actions = task.get("actions") or []
    if actions and isinstance(actions[0], dict):
        phase = str(actions[0].get("phase") or "").strip()
        if phase:
            return phase
    return "run"


def _next_daily_window(schedule, now, zone):
    start = _parse_clock(schedule["start"])
    stop = _parse_clock(schedule["stop"])
    weekdays = schedule.get("weekdays")
    allowed = set(weekdays) if weekdays else None
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        if allowed is not None and day.weekday() not in allowed:
            continue
        start_at = datetime.combine(day, start, tzinfo=zone)
        stop_day = day if stop > start else day + timedelta(days=1)
        stop_at = datetime.combine(stop_day, stop, tzinfo=zone)
        for candidate, phase in ((start_at, "start"), (stop_at, "stop")):
            if candidate > now:
                return candidate, phase
    return None, None


def _cron_day_matches(candidate, cron):
    dom_match = candidate.day in cron["days"]
    dow_match = candidate.weekday() in cron["weekdays"]
    if cron["dom_restricted"] and cron["dow_restricted"]:
        return dom_match or dow_match
    return dom_match and dow_match


def _next_cron(cron, now):
    start = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for offset in range(366 * 24 * 60):
        candidate = start + timedelta(minutes=offset)
        if (candidate.minute in cron["minutes"]
                and candidate.hour in cron["hours"]
                and candidate.month in cron["months"]
                and _cron_day_matches(candidate, cron)):
            return candidate
    return None


def _next_interval(task, seconds, now, zone):
    anchor = None
    if task.get("next_run_at"):
        try:
            anchor = _parse_at(task["next_run_at"], zone)
        except ValueError:
            anchor = None
    if anchor is not None and anchor > now:
        return anchor
    if anchor is None:
        return now + timedelta(seconds=seconds)
    elapsed = (now - anchor).total_seconds()
    steps = int(elapsed // seconds) + 1
    return anchor + timedelta(seconds=steps * seconds)


def next_occurrence(task, now=None):
    """计算 task 的下一次触发时间与阶段；无后续时返回 None。"""
    schedule = validate_schedule(task.get("schedule"))
    zone = _zone(task.get("timezone"))
    current = _to_zone(now, zone)
    kind = schedule["type"]
    phase = _phase_for(task, schedule)
    if kind == "daily_window":
        candidate, phase = _next_daily_window(schedule, current, zone)
    elif kind == "once":
        candidate = _parse_at(schedule["at"], zone)
        if candidate <= current:
            return None
    elif kind == "cron":
        candidate = _next_cron(_parse_cron(schedule["expr"]), current)
    else:
        candidate = _next_interval(task, schedule["seconds"], current, zone)
    if candidate is None:
        return None
    return {"at": candidate.strftime(TIME_FORMAT), "phase": phase}


def phase_for_slot(task, scheduled_at):
    """根据已到期的 next_run_at 判断本次应执行 start 还是 stop。"""
    schedule = validate_schedule(task.get("schedule"))
    if schedule["type"] != "daily_window":
        return _phase_for(task, schedule)
    text = str(scheduled_at or "").strip()
    for phase in ("start", "stop"):
        clock = _parse_clock(schedule[phase])
        if (text.endswith(clock.strftime("%H:%M:%S"))
                or text.endswith(clock.strftime("%H:%M"))):
            return phase
    return None


def advance_task(store, task, now=None):
    """计算下一次触发并写回 next_run_at；返回 next_occurrence 的结果。"""
    nxt = next_occurrence(task, now)
    store.update_task(task["id"], next_run_at=nxt["at"] if nxt else None)
    return nxt
