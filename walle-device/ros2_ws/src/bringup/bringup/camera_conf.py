#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摄像头与录像配置（walle_camera.conf）的定位、读取与校验。

配置文件由 scripts/auto_gen_camera_conf.sh 交互式生成，JSON 格式。

schema_version == 1（camera / record 分块）：
  { "schema_version":1,
    "camera": { "name":..., "device":..., "format":..., "width":..., "height":..., "framerate":... },
    "record": { "enabled":..., "dir_name":..., "storage_path":...,
                "storage_limit_gb":..., "segment_duration_sec":...,
                "auto_record":... } }

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

# 录像默认值
DEFAULT_RECORD_DIR_NAME = 'recordings'
DEFAULT_STORAGE_LIMIT_GB = 16.0
DEFAULT_SEGMENT_DURATION_SEC = 3600
MIN_FREE_SPACE_GB = 6.0


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


def _default_record_conf():
    """返回录像配置默认值。"""
    return {
        'enabled': True,
        'dir_name': DEFAULT_RECORD_DIR_NAME,
        'storage_path': '',
        'storage_limit_gb': DEFAULT_STORAGE_LIMIT_GB,
        'segment_duration_sec': DEFAULT_SEGMENT_DURATION_SEC,
        'auto_record': True,
    }


def _validate_camera_block(block, path):
    """校验 camera 块，返回规范化 dict；失败抛 ValueError。"""
    if not isinstance(block, dict):
        raise ValueError('camera 必须是 JSON 对象')
    fmt = str(block.get('format', '')).strip().upper()
    fmt = FORMAT_ALIASES.get(fmt, fmt)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            'camera.format 不支持：%r（仅支持 %s）'
            % (block.get('format'), '/'.join(SUPPORTED_FORMATS)))
    try:
        width = int(block.get('width'))
        height = int(block.get('height'))
        framerate = float(block.get('framerate'))
    except (TypeError, ValueError):
        raise ValueError('camera 的 width/height/framerate 必须是数字')
    if width <= 0 or height <= 0 or framerate <= 0:
        raise ValueError('camera 的 width/height/framerate 必须大于 0')
    device = str(block.get('device', '')).strip()
    if not device.startswith('/dev/'):
        raise ValueError('camera.device 必须是 /dev/videoN 设备节点')
    name = str(block.get('name') or os.path.basename(device)).strip()
    return {
        'name': name,
        'device': device,
        'format': fmt,
        'width': width,
        'height': height,
        'framerate': framerate,
    }


def _validate_record_block(block, path):
    """校验 record 块，返回规范化 dict；缺失或非法字段用默认值填充。"""
    default = _default_record_conf()
    if block is None:
        return default
    if not isinstance(block, dict):
        # 非对象：退回默认值，不影响摄像头功能
        return default
    rec = dict(default)
    try:
        rec['enabled'] = bool(block.get('enabled', default['enabled']))
    except (TypeError, ValueError):
        pass
    dir_name = str(block.get('dir_name') or '').strip()
    if dir_name and all(c.isalnum() or c in '_-' for c in dir_name):
        rec['dir_name'] = dir_name
    storage_path = str(block.get('storage_path') or '').strip()
    if storage_path:
        rec['storage_path'] = os.path.abspath(os.path.expanduser(storage_path))
    try:
        limit = float(block.get('storage_limit_gb', default['storage_limit_gb']))
        if limit >= 1.0:
            rec['storage_limit_gb'] = limit
    except (TypeError, ValueError):
        pass
    try:
        seg = int(block.get('segment_duration_sec', default['segment_duration_sec']))
        if 60 <= seg <= 24 * 3600:
            rec['segment_duration_sec'] = seg
    except (TypeError, ValueError):
        pass
    rec['auto_record'] = bool(block.get('auto_record', default['auto_record']))
    return rec


def load_camera_conf(explicit=None):
    """读取并校验配置，返回 (conf, path, error)。

    conf 为规范化后的 dict，固定包含 camera 与 record 两个子 dict；
    失败时 conf 为 None，error 为可读原因。
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

    try:
        camera_block = data.get('camera')
        if camera_block is None:
            return None, path, '%s 缺少 camera 块' % path
        camera = _validate_camera_block(camera_block, path)
        record = _validate_record_block(data.get('record'), path)
    except ValueError as exc:
        return None, path, '%s 配置错误：%s' % (path, exc)

    return {
        'schema_version': SCHEMA_VERSION,
        'camera': camera,
        'record': record,
    }, path, None


def describe_camera_conf(conf):
    cam = conf.get('camera', {})
    fps = cam.get('framerate', 0)
    fps_text = ('%g' % fps) if isinstance(fps, float) else str(fps)
    rec = conf.get('record') or {}
    rec_text = ''
    if rec:
        rec_text = '；录像：%sGB上限/%ds分片' % (
            rec.get('storage_limit_gb', '?'),
            rec.get('segment_duration_sec', '?'))
    return '%s %s %dx%d@%sfps%s' % (
        cam.get('name', '?'), cam.get('format', '?'),
        cam.get('width', 0), cam.get('height', 0), fps_text, rec_text)
