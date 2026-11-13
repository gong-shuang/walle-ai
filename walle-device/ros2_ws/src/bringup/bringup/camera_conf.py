#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摄像头配置（walle_camera.conf）的定位、读取与校验。

配置文件由 scripts/auto_gen_camera_conf.sh 交互式生成，JSON 格式。
运行时按以下顺序查找：
  1. 显式参数 / 环境变量 WALLE_CAMERA_CONF
  2. /etc/walle-ai/walle_camera.conf（deploy.sh 安装）
  3. $TURBOPI_HOME/scripts/etc/walle_camera.conf（开发环境）
"""

import json
import os

SCHEMA_VERSION = 1
CONF_NAME = 'walle_camera.conf'
SUPPORTED_FORMATS = ('MJPG', 'YUYV')
FORMAT_ALIASES = {'MJPEG': 'MJPG'}


def candidate_paths(explicit=None):
    """按优先级返回候选配置文件路径；显式指定时不再回退。"""
    if explicit:
        return [os.path.expanduser(explicit)]
    env_path = os.environ.get('WALLE_CAMERA_CONF', '').strip()
    if env_path:
        return [os.path.expanduser(env_path)]
    paths = [os.path.join('/etc', 'walle-ai', CONF_NAME)]
    home = (os.environ.get('TURBOPI_HOME')
            or os.environ.get('WALLE_HOME')
            or '').strip()
    if home:
        paths.append(os.path.join(home, 'scripts', 'etc', CONF_NAME))
    return paths


def find_camera_conf(explicit=None):
    for path in candidate_paths(explicit):
        if os.path.isfile(path):
            return path
    return None


def load_camera_conf(explicit=None):
    """读取并校验配置，返回 (conf, path, error)。

    conf 为规范化后的 dict；失败时 conf 为 None，error 为可读原因。
    """
    path = find_camera_conf(explicit)
    if path is None:
        return None, None, (
            '未找到 %s，请先运行 scripts/auto_gen_camera_conf.sh 生成'
            '（已查找：%s）'
            % (CONF_NAME, ', '.join(candidate_paths(explicit))))
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return None, path, '%s 读取失败：%s' % (path, exc)
    if not isinstance(data, dict):
        return None, path, '%s 格式错误：根节点必须是 JSON 对象' % path

    version = data.get('schema_version')
    if version != SCHEMA_VERSION:
        return None, path, (
            '%s 版本不支持：schema_version=%r（期望 %d）'
            % (path, version, SCHEMA_VERSION))

    fmt = str(data.get('format', '')).strip().upper()
    fmt = FORMAT_ALIASES.get(fmt, fmt)
    if fmt not in SUPPORTED_FORMATS:
        return None, path, (
            '%s 格式不支持：%r（仅支持 %s）'
            % (path, data.get('format'), '/'.join(SUPPORTED_FORMATS)))

    try:
        width = int(data.get('width'))
        height = int(data.get('height'))
        framerate = float(data.get('framerate'))
    except (TypeError, ValueError):
        return None, path, '%s 的 width/height/framerate 必须是数字' % path
    if width <= 0 or height <= 0 or framerate <= 0:
        return None, path, '%s 的 width/height/framerate 必须大于 0' % path

    device = str(data.get('device', '')).strip()
    if not device.startswith('/dev/'):
        return None, path, '%s 的 device 必须是 /dev/videoN 设备节点' % path

    name = str(data.get('name') or os.path.basename(device)).strip()
    return {
        'schema_version': SCHEMA_VERSION,
        'name': name,
        'device': device,
        'format': fmt,
        'width': width,
        'height': height,
        'framerate': framerate,
    }, path, None


def describe_camera_conf(conf):
    fps = conf['framerate']
    fps_text = ('%g' % fps) if isinstance(fps, float) else str(fps)
    return '%s %s %dx%d@%sfps' % (
        conf['name'], conf['format'], conf['width'], conf['height'], fps_text)
