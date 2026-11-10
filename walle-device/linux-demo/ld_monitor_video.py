#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频推流：启动/停止、进程看护、中断告警与 video/start|stop 应答。"""
import logging
import os
import re
import subprocess
import threading
import time

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *
from ld_camera import *
from ld_audio import *
from ld_media import *
from ld_media import _child_preexec

class MonitorVideoMixin:
    """推流启动/停止、进程看护与 video 应答。"""
    # ---------- video/start 推流 ----------

    def _publish_video_result(self, req, result_code, cause, extra=None):
        """统一推流结果报文：result 固定语义 -1/0/-2，细节放附加字段"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": HOSTNAME,
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
        self.publish_json(RES_VIDEO_START_RESULT, payload)
        logging.info("推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     device_topic(RES_VIDEO_START_RESULT), payload["request_id"] or "-",
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

    @staticmethod
    def _log_pump(proc, sink):
        """
        把 ffmpeg 的实时输出持续写进日志文件，直到管道关闭。
        按块读而不是按行读：ffmpeg 的帧统计（frame= ... fps= ...）是用 \r 原地刷新的，
        按行读会一直等不到换行，日志里就看不到实时进度。
        """
        try:
            fd = proc.stdout.fileno()
            while True:
                chunk = os.read(fd, 4096)   # 阻塞到有数据，有多少写多少
                if not chunk:
                    break
                sink.write(chunk)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            sink.close()

    def _video_watch(self, proc, room_number, sink):
        """推流进程看护：退出后清理状态；非主动退出时向 App 上报 video_lost。"""
        code = proc.wait()
        tail = sink.tail()
        sink.close()
        with self.video_lock:
            # 看护线程可能晚于新推流启动：只有仍是当前进程才算「中断」，
            # 否则是参数切换/重启时被主动替换的旧进程。
            was_active = self.video_proc is proc
            if was_active:
                self.video_proc = None
                self.video_meta = {}
        if getattr(proc, "_intentional_stop", False):
            logging.info("推流已按要求停止（room=%s, exit=%s）", room_number, code)
            return
        logging.info("推流进程已退出（room=%s, exit=%s）", room_number, code)
        if was_active:
            self._report_video_lost(room_number, code, tail, sink.path)

    def _report_video_lost(self, room_number, exit_code, tail, log_path):
        """非主动退出：记录并向 App 发布 video_lost 告警（App 据此提示用户重试）。"""
        lost = {
            "room_number": room_number,
            "exit_code": exit_code,
            "log_file": log_path,
            "lost_at": now_str(),
        }
        with self.video_lock:
            self.video_lost = lost
        self._event(
            "alarm", "warning", "video", "video_lost",
            f"推流中断（room={room_number}，exit={exit_code}），"
            f"请检查摄像头/网络后重新推流",
            {
                "room_number": room_number,
                "exit_code": exit_code,
                "log_file": log_path,
                "tail": (tail or "")[-500:],
            },
        )

    def _clear_video_lost(self, room_number, video_device):
        """重新推流成功：清除上一次中断记录并发 recover，避免告警一直挂着。"""
        with self.video_lock:
            lost = self.video_lost
            self.video_lost = None
        if not lost:
            return
        self._event(
            "recover", "info", "video", "video_lost",
            f"推流已恢复（room={room_number}）",
            {
                "room_number": room_number,
                "video_device": video_device,
                "previous_exit_code": lost.get("exit_code"),
                "lost_at": lost.get("lost_at"),
            },
        )

    def _start_video_stream(self, video_device, room_number, audio=None, rtmp_url=None,
                            video_size=None):
        """
        启动 ffmpeg 推流。返回 (proc, error)：
          - proc 不为 None：启动宽限期内未退出，视为推流成功
          - proc 为 None：error 为失败原因

        audio: detect_microphone() 的结果，决定是否混入音频（见 build_ffmpeg_cmd）。
        rtmp_url: 已构造好的推流地址，避免重复构造。
        video_size: 本次推流分辨率；不传则用配置默认值。
        """
        rtmp = rtmp_url or build_rtmp_url(room_number)
        size = video_size or VIDEO_VIDEO_SIZE
        cmd = build_ffmpeg_cmd(video_device, room_number, audio,
                               rtmp_url=rtmp, video_size=size)
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
                preexec_fn=_child_preexec(),     # 父进程崩溃/被 kill 时子进程自动退出
            )
        except FileNotFoundError:
            sink.close()
            return None, "未安装 ffmpeg，请执行: sudo apt install -y ffmpeg"
        except Exception as e:  # noqa: BLE001
            sink.close()
            return None, f"启动 ffmpeg 失败: {e}"

        threading.Thread(target=self._log_pump, args=(proc, sink),
                         daemon=True, name="video-log").start()
        logging.info("已启动推流: %s（日志: %s）", mask_rtmp_url(rtmp), sink.path)

        # 宽限期内就退出，说明参数/设备/网络有问题，直接判失败
        try:
            code = proc.wait(timeout=VIDEO_STARTUP_GRACE)
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
                "rtmp_url": mask_rtmp_url(rtmp),
                "video_size": size,
                "audio": audio or {"available": False, "reason": None},
            }
        threading.Thread(target=self._video_watch, args=(proc, room_number, sink),
                         daemon=True, name="video-watch").start()
        return proc, None

    def handle_video_request(self, payload):
        # 该主题必须校验 uuid
        ok, data = self.verify_uuid(payload, REQ_VIDEO_START)
        if not ok:
            return

        video_device = str(data.get("video_device") or "").strip()
        room_number = str(data.get("room_number") or "").strip()
        # 注意：这里曾重建 dict 时漏掉 request_id，导致应答回显成空串、App 严格配对后丢弃结果
        req = self._video_req(data, video_device, room_number)

        if not VIDEO_ENABLE:
            self._publish_video_result(req, -2, "命令执行失败",
                                       {"error": "推流能力已关闭（VIDEO_ENABLE=False）"})
            return

        # 1) 摄像头设备是否存在 + USB 采集节点/分辨率 preflight
        if not video_device or not os.path.exists(video_device):
            self._publish_video_result(
                req, -1, "没有此摄像头设备",
                {"error": f"设备节点不存在: {video_device or '<空>'}",
                 "cameras": self.camera_nodes()})
            return
        ok_cam, cam_error = camera_preflight(video_device)
        if not ok_cam:
            self._publish_video_result(
                req, -1, "没有此摄像头设备",
                {"error": cam_error, "cameras": self.camera_nodes()})
            return
        video_size, size_note = pick_video_size(video_device)
        if not video_size:
            self._publish_video_result(
                req, -1, "没有此摄像头设备",
                {"error": size_note, "cameras": self.camera_nodes()})
            return
        if size_note:
            logging.warning("%s", size_note)

        # 2) 房间号白名单（要拼进 RTMP URL，必须严格校验）
        if not re.match(VIDEO_ROOM_PATTERN, room_number):
            self._publish_video_result(
                req, -2, "命令执行失败",
                {"error": "room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）"})
            return

        # 2.1) Oryx 推流 secret：每台服务器不同，部署时用 WHIP_SECRET 注入。
        #      没配置时不提前拦下，ffmpeg 会因为鉴权失败一直重连，看起来像“推流成功但黑屏”。
        ok_url, url_error = rtmp_url_ready()
        if not ok_url:
            self._publish_video_result(req, -2, "命令执行失败", {"error": url_error})
            return
        rtmp_url = build_rtmp_url(room_number)
        masked_rtmp = mask_rtmp_url(rtmp_url)

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
                "rtmp_url": masked_rtmp,
                "video_size": cur_meta.get("video_size"),
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
        proc, err = self._start_video_stream(
            video_device, room_number, audio, rtmp_url, video_size)
        extra = {
            "rtmp_url": masked_rtmp,
            "video_size": video_size,
            "framerate": VIDEO_FRAMERATE,
            "command": " ".join(build_ffmpeg_cmd(
                video_device, room_number, audio,
                rtmp_url=rtmp_url, video_size=video_size)).replace(
                    rtmp_url, masked_rtmp),
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
        extra["startup_grace_sec"] = VIDEO_STARTUP_GRACE
        self._publish_video_result(req, 0, "ok", extra)
        self._clear_video_lost(room_number, video_device)

    # ---------- video/stop/send 停止推流 ----------

    def _publish_video_stop_result(self, request_id, result_code, cause, extra=None):
        """统一停止推流结果报文：result -1 当前没有设备在推流 / 0 ok"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "request_id": str(request_id or ""),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(RES_VIDEO_STOP_RESULT, payload)
        logging.info("停止推流结果 -> %s: request_id=%s result=%s cause=%s%s",
                     device_topic(RES_VIDEO_STOP_RESULT), payload["request_id"] or "-",
                     result_code, cause,
                     f"（{extra['error']}）" if extra and extra.get("error") else "")
        return payload

    def handle_video_stop(self, payload):
        # 该主题必须校验 uuid
        ok, data = self.verify_uuid(payload, REQ_VIDEO_STOP)
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
