#!/usr/bin/env python3
"""P1-5.2：视频推流房间号校验测试。"""

import pytest

from video_webrtc_bridge.node import DEFAULT_ROOM_PATTERN, valid_room_number


@pytest.mark.parametrize("room", [
    "a",
    "A1",
    "room_01",
    "room-01",
    "_",
    "-",
    "x" * 64,
])
def test_valid_room_numbers(room):
    assert valid_room_number(room) is True


@pytest.mark.parametrize("room", [
    "",
    None,
    "   ",
    "room 1",
    "room/01",
    "room/../01",
    "room?secret=x",
    "房间",
    "x" * 65,
    "room\n",
])
def test_invalid_room_numbers(room):
    assert valid_room_number(room) is False


def test_custom_pattern_is_fully_matched():
    # pattern 未加锚点也必须整串匹配，避免 room/../x 这类前缀通过
    assert valid_room_number("ab", r"[a-z]+") is True
    assert valid_room_number("ab/x", r"[a-z]+") is False


def test_default_pattern_value():
    assert DEFAULT_ROOM_PATTERN == r"^[A-Za-z0-9_-]{1,64}$"
