#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video/start|stop 处理（独立运行模式；ROS 下由 node.py 覆盖为服务调用）。"""

import logging
import os
import re
import subprocess
import threading
import time

from . import config
from .audio import detect_microphone
from .identity import now_str, now_ts
from .media import (build_ffmpeg_cmd, build_rtmp_url, make_log_sink,
                    mask_rtmp_url, pump_process_output, rtmp_secret_ready)


class VideoMixin:
    def _publish_video_result(self, req, result_code, cause, extra=None):
        """统一推流结果报文：result 固定语义 -1/0/-2，细节放附加字段"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "video_device": req.get("video_device"),
            "room_number": req.get("room_number"),
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_VIDEO_START_RESULT, payload)
        logging.info("推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     config.device_topic(config.RES_VIDEO_START_RESULT), payload["request_id"] or "-",
                     result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def _stop_video_stream(self, reason=""):
        """停掉当前推流（如果有），返回是否真的停了一个在跑的进程"""
        with self.video_lock:
            proc = self.video_proc
            meta = self.video_meta
            self.video_proc = None
            self.video_meta = {}
        if not proc or proc.poll() is not None:
            return False
        # 标记为主动停止，看护线程据此只记 info 日志，不报"异常退出"
        proc._intentional_stop = True
        try:
            proc.terminate()  # SIGTERM，ffmpeg 会自己收尾
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            logging.exception("停止推流进程失败")
        logging.info("已停止推流%s（room=%s, pid=%s）",
                     f"：{reason}" if reason else "", meta.get("room_number"), meta.get("pid"))
        return True

    def _video_watch(self, proc, room_number, sink):
        """推流进程看护：退出后清理状态并记录原因"""
        try:
            code = proc.wait()
        finally:
            tail = sink.tail()
            sink.close()
            with self.video_lock:
                if self.video_proc is proc:
                    self.video_proc = None
                    self.video_meta = {}
        if getattr(proc, "_intentional_stop", False):
            logging.info("推流已按要求停止（room=%s, exit=%s）", room_number, code)
        elif code == 0:
            logging.info("推流进程已退出（room=%s, exit=0）", room_number)
        else:
            logging.warning("推流进程异常退出（room=%s, exit=%s）%s",
                            room_number, code, f"，stderr: {tail}" if tail else "")

    def _start_video_stream(self, video_device, room_number, audio=None):
        """
        启动 ffmpeg 推流。返回 (proc, error)：
          - proc 不为 None：启动宽限期内未退出，视为推流成功
          - proc 为 None：error 为失败原因

        audio: detect_microphone() 的结果，决定是否混入音频（见 build_ffmpeg_cmd）。
        """
        cmd = build_ffmpeg_cmd(video_device, room_number, audio)
        rtmp = build_rtmp_url(room_number)
        # 日志文件开头写一段分隔头，记录这次推流的时间/房间号/设备/实际命令
        header = (
            f"\n===== {now_str()} 启动推流 room={room_number} device={video_device} =====\n"
            f"$ {' '.join(cmd).replace(rtmp, mask_rtmp_url(rtmp))}\n"
        ).encode("utf-8", "replace")
        sink = make_log_sink(header)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,          # 由泵线程实时转存到日志文件
                stderr=subprocess.STDOUT,        # ffmpeg 的日志本来就走 stderr，合并后一起收
                bufsize=0,                       # 不缓冲，泵线程读到多少就写多少
                start_new_session=True,          # 独立进程组，避免 Ctrl+C 连带影响
            )
        except FileNotFoundError:
            sink.close()
            return None, "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
        except Exception as e:  # noqa: BLE001
            sink.close()
            return None, f"启动 ffmpeg 失败: {e}"

        threading.Thread(target=pump_process_output, args=(proc, sink),
                         daemon=True, name="video-log").start()
        logging.info("已启动推流: %s（日志: %s）", mask_rtmp_url(rtmp), sink.path)

        # 宽限期内就退出，说明参数/设备/网络有问题，直接判失败
        try:
            code = proc.wait(timeout=config.VIDEO_STARTUP_GRACE)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            time.sleep(0.2)          # 给泵线程一点时间把最后的输出写完
            tail = sink.error_line()
            msg = f"ffmpeg 退出码 {code}（日志: {sink.path}）"
            if sink.fallback_error:
                msg += f"（日志文件不可写: {sink.fallback_error}）"
            sink.close()
            return None, msg + (f": {tail}" if tail else "")

        with self.video_lock:
            self.video_proc = proc
            self.video_meta = {
                "room_number": room_number,
                "video_device": video_device,
                "pid": proc.pid,
                "started_at": now_str(),
                "started_ts": time.time(),
                "rtmp_url": mask_rtmp_url(build_rtmp_url(room_number)),
                "audio": audio or {"available": False, "reason": None},
            }
        threading.Thread(target=self._video_watch, args=(proc, room_number, sink),
                         daemon=True, name="video-watch").start()
        return proc, None

    def handle_video_request(self, payload):
        # 该主题必须校验 uuid
        ok, data = self.verify_uuid(payload, config.REQ_VIDEO_START)
        if not ok:
            return

        video_device = str(data.get("video_device") or "").strip()
        room_number = str(data.get("room_number") or "").strip()
        # 注意：这里曾重建 dict 时漏掉 request_id，导致应答回显成空串、App 严格配对后丢弃结果
        req = self._video_req(data, video_device, room_number)

        if not config.VIDEO_ENABLE:
            self._publish_video_result(req, -2, "命令执行失败",
                                       {"error": "推流能力已关闭（VIDEO_ENABLE=False）"})
            return

        # 1) 摄像头设备是否存在
        if not re.match(r"^/dev/video\d+$", video_device) or not os.path.exists(video_device):
            self._publish_video_result(
                req, -1, "没有此摄像头设备",
                {"error": f"设备节点不存在: {video_device or '<空>'}"})
            return

        # 2) 房间号白名单（要拼进 RTMP URL，必须严格校验）
        if not re.match(config.VIDEO_ROOM_PATTERN, room_number):
            self._publish_video_result(
                req, -2, "命令执行失败",
                {"error": "room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）"})
            return

        # 2.1) Oryx 推流 secret：每台服务器不同，部署时用 WHIP_SECRET 注入。
        #      没配置时不提前拦下，ffmpeg 会因为鉴权失败一直重连，看起来像“推流成功但黑屏”。
        if not rtmp_secret_ready():
            self._publish_video_result(
                req, -2, "命令执行失败",
                {"error": "未配置 Oryx 推流 secret（请在 /etc/walle-ai.conf 设置 WHIP_SECRET）"})
            return

        # 3) 已有推流时：video_device 和 room_number 都一样 → 不处理（不重连、不重启）
        #    任一不同 → 停掉旧的，用新参数重新推流
        with self.video_lock:
            cur_proc = self.video_proc
            cur_meta = dict(self.video_meta)
        streaming = bool(cur_proc is not None and cur_proc.poll() is None)
        if streaming and (
            cur_meta.get("video_device") == video_device
            and cur_meta.get("room_number") == room_number
        ):
            logging.info(
                "推流请求与当前推流一致（device=%s room=%s），不重复推流",
                video_device, room_number,
            )
            self._publish_video_result(req, 0, "ok", {
                "action": "already_streaming",
                "note": "相同 video_device 和 room_number 已在推流，本次不做处理",
                "video_device": video_device,
                "room_number": room_number,
                "rtmp_url": mask_rtmp_url(build_rtmp_url(room_number)),
                "pid": cur_meta.get("pid"),
                "started_at": cur_meta.get("started_at"),
                "audio": cur_meta.get("audio") or {"available": False, "reason": None},
                "uptime_sec": round(
                    time.time() - (cur_meta.get("started_ts") or time.time()), 1
                ),
            })
            return

        # 3.1) 参数有变化（或旧进程已退出）：先停旧的，保证同一时刻只有一路推流
        previous_room = cur_meta.get("room_number") if streaming else None
        previous_device = cur_meta.get("video_device") if streaming else None
        self._stop_video_stream("收到新的推流请求")

        # 4) 检测麦克风：有可用录音源就带上声音，否则纯视频（-an）
        audio = detect_microphone()
        if audio["available"]:
            logging.info("检测到麦克风，推流将混入音频（device=%s, %s/%s）",
                         audio["device"], audio["codec"], audio["bitrate"])
        else:
            logging.info("未启用音频推流：%s", audio["reason"] or "未检测到麦克风")

        # 5) 启动 ffmpeg 推流
        proc, err = self._start_video_stream(video_device, room_number, audio)
        extra = {
            "rtmp_url": mask_rtmp_url(build_rtmp_url(room_number)),
            "command": " ".join(build_ffmpeg_cmd(video_device, room_number, audio)).replace(
                build_rtmp_url(room_number), mask_rtmp_url(build_rtmp_url(room_number))),
            "pid": proc.pid if proc else None,
            "audio": audio,
        }
        extra["action"] = "restarted" if previous_room else "started"
        if previous_room:
            extra["previous_room_number"] = previous_room
        if previous_device:
            extra["previous_video_device"] = previous_device
        if proc is None:
            extra["action"] = "failed"
            extra["error"] = err
            self._publish_video_result(req, -2, "命令执行失败", extra)
            return
        extra["started_at"] = self.video_meta.get("started_at")
        extra["startup_grace_sec"] = config.VIDEO_STARTUP_GRACE
        self._publish_video_result(req, 0, "ok", extra)

    # ---------- video/stop/send 停止推流 ----------

    def _publish_video_stop_result(self, request_id, result_code, cause, extra=None):
        """统一停止推流结果报文：result -1 当前没有设备在推流 / 0 ok"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": config.HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(config.RES_VIDEO_STOP_RESULT, payload)
        logging.info("停止推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     config.device_topic(config.RES_VIDEO_STOP_RESULT), payload["request_id"] or "-",
                     result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def handle_video_stop(self, payload):
        # 该主题必须校验 uuid
        ok, data = self.verify_uuid(payload, config.REQ_VIDEO_STOP)
        if not ok:
            return

        rid = self._rid(data)
        req_room = str(data.get("room_number") or "").strip()

        # 先取当前推流状态（meta 会在停止后清空，所以要提前留一份）
        with self.video_lock:
            proc = self.video_proc
            meta = dict(self.video_meta)
        running = bool(proc is not None and proc.poll() is None)

        if not running:
            extra = {"room_number": req_room or None, "stopped": False}
            self._publish_video_stop_result(rid, -1, "当前没有设备在推流", extra)
            return

        # 可选：带 room_number 时做一致性校验，避免停错流
        current_room = meta.get("room_number")
        if req_room and current_room and req_room != current_room:
            self._publish_video_stop_result(
                rid, -1, "当前没有设备在推流",
                {"room_number": req_room, "stopped": False,
                 "error": f"当前推流房间号是 {current_room}，与请求的 {req_room} 不一致"})
            return

        stopped = self._stop_video_stream("收到停止推流请求")
        if not stopped:
            self._publish_video_stop_result(rid, -1, "当前没有设备在推流",
                                            {"room_number": current_room, "stopped": False})
            return

        self._publish_video_stop_result(rid, 0, "ok", {
            "room_number": current_room,
            "video_device": meta.get("video_device"),
            "pid": meta.get("pid"),
            "started_at": meta.get("started_at"),
            "audio": meta.get("audio") or {"available": False, "reason": None},
            "stopped": True,
        })

    # ---------- phone/audio/send|stop 手机麦上行播放 ----------
