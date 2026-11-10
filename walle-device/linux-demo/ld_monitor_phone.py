#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机麦上行：WHEP 播放进程看护与 phone/audio/send|stop 应答。"""
import logging
import os
import re
import subprocess
import sys
import threading
import time

import ld_config as config
from ld_config import *
from ld_protocol import *
from ld_utils import *
from ld_media import *
from ld_media import _child_preexec

class MonitorPhoneMixin:
    """手机麦上行播放进程看护与 phone/audio 应答。"""
    # ---------- phone/audio/send|stop 手机麦上行播放 ----------

    def _phone_audio_url(self, room_number):
        """手机推音频的流名 = 视频房间号 + _audio，避免与设备自己的视频流抢同一路 stream。

        媒体面走 WebRTC：App 用 WHIP 推、本机用 WHEP 拉。相比 RTMP 拉流，RTC 走 UDP、
        按最新帧转发，端到端延迟约 200ms（RTMP+ffmpeg 实测会被各级缓冲拖到数秒）。
        拉流地址带短期 token，由 Nginx secure_link 校验（见 doc/service_deploy.md §6.2）。
        """
        stream = room_number + PHONE_AUDIO_STREAM_SUFFIX
        expires = int(time.time()) + PHONE_AUDIO_WHEP_TOKEN_TTL
        token = whep_token(stream, expires)
        return PHONE_AUDIO_WHEP_URL_TEMPLATE.format(
            stream=stream, expires=expires, token=token)

    def _publish_phone_audio_result(self, resource, req, result_code, cause, extra=None):
        """手机音频动作的结果报文（result 语义与 video 一致：0 成功 / -1 无流 / -2 失败）"""
        payload = {
            "device_id": config.DEVICE_ID,
            "uuid": config.DEVICE_UUID,
            "hostname": HOSTNAME,
            "ip": self.lan_ip,
            "room_number": req.get("room_number"),
            "request_id": self._rid(req),
            "result": result_code,
            "cause": cause,
            "timestamp": now_ts(),
            "datetime": now_str(),
        }
        if extra:
            payload.update(extra)
        self.publish_json(resource, payload)
        logging.info("手机音频结果 -> %s: request_id=%s result=%s cause=%s",
                     device_topic(resource), payload["request_id"] or "-", result_code, cause)
        # 失败细节（播放器退出码/日志路径/stderr 摘要）App 不订阅 result，
        # 本地日志必须打出来，否则设备侧只能看到「失败」看不到原因
        err = payload.get("error")
        if result_code != 0 and err:
            logging.warning("手机音频失败详情: %s", err)
        return payload

    def _stop_phone_audio(self, reason=""):
        """停掉正在播放的手机音频（如果有），返回是否真的停了一个在跑的进程"""
        with self.audio_lock:
            proc = self.audio_proc
            meta = self.audio_meta
            self.audio_proc = None
            self.audio_meta = {}
        if not proc or proc.poll() is not None:
            return False
        proc._intentional_stop = True
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            logging.exception("停止手机音频播放失败")
        logging.info("已停止手机音频播放%s（room=%s, pid=%s）",
                     f"：{reason}" if reason else "", meta.get("room_number"), meta.get("pid"))
        return True

    def _build_phone_audio_cmd(self, room_number, url=None):
        """WHEP 拉 WebRTC 音频 → 本机声卡（独立进程 whep_audio_player.py）。

        为什么不用 ffmpeg 拉 RTMP：RTMP 走 TCP，且服务端/解码器/声卡各级缓冲会把
        「手机说话 → 扬声器出声」拖到数秒（实测 ~10s）。WebRTC 走 UDP、按最新帧转发，
        端到端约 200ms，与视频方向（WHEP）同级。
        """
        cmd = [sys.executable, PHONE_AUDIO_PLAYER_SCRIPT,
               "--url", url or self._phone_audio_url(room_number),
               "--latency", PHONE_AUDIO_LATENCY,
               "--first-frame-timeout", str(PHONE_AUDIO_FIRST_FRAME_TIMEOUT),
               "--idle-timeout", str(PHONE_AUDIO_IDLE_TIMEOUT)]
        if PHONE_AUDIO_DEVICE:
            cmd += ["--device", PHONE_AUDIO_DEVICE]
        return cmd

    def _phone_audio_watch(self, proc, room_number, sink):
        """播放进程看护：退出后清理状态；非主动退出时向 App 上报 phone_audio_lost。"""
        code = proc.wait()
        tail = sink.tail()
        sink.close()
        with self.audio_lock:
            was_active = self.audio_proc is proc
            if was_active:
                self.audio_proc = None
                self.audio_meta = {}
        if getattr(proc, "_intentional_stop", False):
            logging.info("手机音频播放已按要求停止（room=%s, exit=%s）", room_number, code)
            return
        logging.info("手机音频播放结束（room=%s, exit=%s）", room_number, code)
        if was_active:
            self._report_phone_audio_lost(room_number, code, tail, sink.path)

    def _report_phone_audio_lost(self, room_number, exit_code, tail, log_path):
        """播放进程非主动退出：按退出码给出可读原因，并向 App 发布告警。"""
        reason = PHONE_AUDIO_EXIT_REASONS.get(exit_code, "播放器异常退出")
        lost = {
            "room_number": room_number,
            "exit_code": exit_code,
            "reason": reason,
            "log_file": log_path,
            "lost_at": now_str(),
        }
        with self.audio_lock:
            self.audio_lost = lost
        self._event(
            "alarm", "warning", "audio", "phone_audio_lost",
            f"手机音频中断（room={room_number}，exit={exit_code}）：{reason}",
            {
                "room_number": room_number,
                "exit_code": exit_code,
                "reason": reason,
                "log_file": log_path,
                "tail": (tail or "")[-500:],
            },
        )

    def _clear_phone_audio_lost(self, room_number):
        """重新开始播放：清除中断记录并发 recover。"""
        with self.audio_lock:
            lost = self.audio_lost
            self.audio_lost = None
        if not lost:
            return
        self._event(
            "recover", "info", "audio", "phone_audio_lost",
            f"手机音频已恢复（room={room_number}）",
            {
                "room_number": room_number,
                "previous_exit_code": lost.get("exit_code"),
                "lost_at": lost.get("lost_at"),
            },
        )

    @staticmethod
    def _wait_process_ready(proc, ready_event, timeout):
        """等待播放器就绪：返回 ready / exit / timeout。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return "exit"
            if ready_event.is_set():
                return "ready"
            time.sleep(0.05)
        return "timeout"

    def _start_phone_audio(self, room_number):
        url = self._phone_audio_url(room_number)
        cmd = self._build_phone_audio_cmd(room_number, url)
        header = (
            f"\n===== {now_str()} 启动手机音频播放 room={room_number} =====\n"
            f"$ {' '.join(cmd).replace(url, mask_rtmp_url(url))}\n"
        ).encode("utf-8", "replace")
        sink = make_log_sink(header)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=True,
                preexec_fn=_child_preexec(),     # 父进程崩溃/被 kill 时子进程自动退出
            )
        except FileNotFoundError:
            sink.close()
            return None, (f"未找到播放器脚本 {PHONE_AUDIO_PLAYER_SCRIPT}"
                          "（请把 linux-demo/whep_audio_player.py 一起部署到同目录）")
        except Exception as e:  # noqa: BLE001
            sink.close()
            return None, f"启动 WHEP 播放器失败: {e}"

        ready = threading.Event()
        threading.Thread(target=self._log_pump, args=(proc, sink, ready),
                         daemon=True, name="phone-audio-log").start()

        # 成功判定对齐「首个音频帧」：等待播放器输出 PLAYBACK READY，
        # 而不是仅靠存活时间——否则手机未推流时会出现「返回成功但无声」。
        wait_sec = max(PHONE_AUDIO_FIRST_FRAME_TIMEOUT + 1, PHONE_AUDIO_STARTUP_GRACE)
        state = self._wait_process_ready(proc, ready, wait_sec)
        code = proc.poll()
        if state == "exit" or code is not None:
            time.sleep(0.2)
            tail = sink.error_line()
            msg = f"播放器退出码 {code}（日志: {sink.path}）"
            if code in PHONE_AUDIO_EXIT_REASONS:
                msg += f"；{PHONE_AUDIO_EXIT_REASONS[code]}"
            sink.close()
            return None, msg + (f": {tail}" if tail else "")
        if state == "timeout":
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:  # noqa: BLE001
                proc.kill()
            tail = sink.error_line()
            sink.close()
            return None, (f"播放器 {wait_sec:g}s 内未开始播放（首帧超时），"
                          f"请确认手机端已推流（日志: {sink.path}）"
                          + (f": {tail}" if tail else ""))

        with self.audio_lock:
            self.audio_proc = proc
            self.audio_meta = {
                "room_number": room_number,
                "pid": proc.pid,
                "started_at": now_str(),
                "started_ts": time.time(),
                "whep_url": self._phone_audio_url(room_number),
                "output": f"sounddevice/{PHONE_AUDIO_DEVICE or 'default'}",
            }
        threading.Thread(target=self._phone_audio_watch,
                         args=(proc, room_number, sink), daemon=True,
                         name="phone-audio-watch").start()
        return proc, None

    def handle_phone_audio_send(self, payload):
        ok, data = self.verify_uuid(payload, REQ_PHONE_AUDIO_SEND)
        if not ok:
            return

        room_number = str(data.get("room_number") or "").strip()
        req = {"room_number": room_number, "request_id": self._rid(data)}

        if not PHONE_AUDIO_ENABLE:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败",
                {"error": "反向音频已关闭（PHONE_AUDIO_ENABLE=False）"})
            return
        if not re.match(VIDEO_ROOM_PATTERN, room_number):
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败",
                {"error": "room_number 不合法（只允许 1-64 位字母/数字/下划线/短横线）"})
            return
        ok_url, url_error = whep_url_ready()
        if not ok_url:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败", {"error": url_error})
            return

        with self.audio_lock:
            cur_proc = self.audio_proc
            cur_meta = dict(self.audio_meta)
        playing = bool(cur_proc is not None and cur_proc.poll() is None)
        if playing and cur_meta.get("room_number") == room_number:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, 0, "ok",
                {"action": "already_playing", "room_number": room_number,
                 "pid": cur_meta.get("pid"), "started_at": cur_meta.get("started_at")})
            return

        self._stop_phone_audio("收到新的音频上行请求")
        proc, err = self._start_phone_audio(room_number)
        if proc is None:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_SEND_RESULT, req, -2, "命令执行失败",
                {"action": "failed", "error": err})
            return
        self._publish_phone_audio_result(
            RES_PHONE_AUDIO_SEND_RESULT, req, 0, "ok",
            {"action": "started", "room_number": room_number,
             "whep_url": self._phone_audio_url(room_number),
             "pid": proc.pid, "output": f"sounddevice/{PHONE_AUDIO_DEVICE or 'default'}"})
        self._clear_phone_audio_lost(room_number)

    def handle_phone_audio_stop(self, payload):
        ok, data = self.verify_uuid(payload, REQ_PHONE_AUDIO_STOP)
        if not ok:
            return

        req_room = str(data.get("room_number") or "").strip()
        req = {"room_number": req_room, "request_id": self._rid(data)}

        with self.audio_lock:
            cur_proc = self.audio_proc
            cur_meta = dict(self.audio_meta)
        playing = bool(cur_proc is not None and cur_proc.poll() is None)
        current_room = cur_meta.get("room_number")
        if playing and req_room and current_room and req_room != current_room:
            self._publish_phone_audio_result(
                RES_PHONE_AUDIO_STOP_RESULT, req, -1, "当前没有音频流",
                {"stopped": False,
                 "error": f"当前播放房间号是 {current_room}，与请求的 {req_room} 不一致"})
            return

        stopped = self._stop_phone_audio("收到停止音频上行请求")
        self._publish_phone_audio_result(
            RES_PHONE_AUDIO_STOP_RESULT, req,
            0 if stopped else -1,
            "ok" if stopped else "当前没有音频流",
            {"stopped": stopped, "room_number": current_room})
