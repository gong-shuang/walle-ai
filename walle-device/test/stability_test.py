#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-5.3 故障注入与长稳测试工具（设备本机使用，勿在生产高峰执行）。

    sudo ./test/stability_test.py health
    sudo ./test/stability_test.py kill
    sudo ./test/stability_test.py camera --device /dev/walle-camera
    sudo ./test/stability_test.py network --mode broker --offline 15
    ./test/stability_test.py soak --hours 24 --interval 10 --require-stream

kill    ：逐个 SIGKILL 关键节点，等待 launch/systemd 拉起新进程。
camera  ：用 usbfs DISCONNECT/CONNECT 模拟拔插摄像头，等待 usb_cam 重启。
network ：默认只拦截到 MQTT Broker 的流量（不影响 SSH 连接）；
          --mode link 才会断开整块网卡，需在串口/本地终端执行。
soak    ：周期采样 RSS/FD/磁盘/温度，输出 CSV，结束时判断泄漏趋势。

串口掉线无法用软件模拟（本机为板载 ttyAMA0），请手动拔插后观察
ros_robot_controller 是否自动重连；kill 该节点可验证节点级恢复。
"""

import argparse
import csv
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime

DEFAULT_SERVICE = "walle-ai.service"
DEFAULT_NODES = (
    "ros_robot_controller",
    "mecanum",
    "usb_cam_node_exe",
    "sonar_controller",
    "video_webrtc_bridge",
    "net_mqtt_bridge",
    "speech",
    "vocal_detect",
    "agent_process",
    "tts_node",
)
SOAK_FIELDS = (
    "timestamp", "time", "service_active", "nrestarts", "node_count",
    "nodes", "rss_kb", "fd_count", "ffmpeg_count", "disk_free_mb",
    "log_mb", "temp_c",
)


def log(msg):
    print(msg, flush=True)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def wait_for(predicate, timeout, interval=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def read_cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def pids_for(name):
    """按命令行匹配进程，排除本工具自身。"""
    result = run(["pgrep", "-f", name])
    pids = []
    for raw in result.stdout.split():
        try:
            pid = int(raw)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        cmdline = read_cmdline(pid)
        # 排除本工具与 ros2 launch 父进程，只对真正的节点进程做故障注入
        skip = any(token in cmdline for token in (
            "stability_test.py", "ros2 launch", ".launch.py"))
        if skip:
            continue
        pids.append(pid)
    return sorted(pids)


def service_active(service):
    return run(["systemctl", "is-active", "--quiet", service]).returncode == 0


def service_restarts(service):
    result = run(["systemctl", "show", "-p", "NRestarts", "--value", service])
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip() or 0)
    except ValueError:
        return None


def parse_conf(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def mqtt_settings(host=None, port=None):
    """优先命令行，其次环境变量，最后读 /etc、脚本同级与 scripts/ 下的配置。"""
    conf = {}
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = ("/etc/walle-ai.conf",
                  os.path.join(here, "walle-ai.conf"),
                  os.path.join(here, os.pardir, "scripts", "walle-ai.conf"))
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.R_OK):
            conf.update(parse_conf(path))
    host = host or os.environ.get("MQTT_HOST") or conf.get("MQTT_HOST") or ""
    port = port or os.environ.get("MQTT_PORT") or conf.get("MQTT_PORT") or 8883
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8883
    return host.strip(), port


def resolve_ips(host, port):
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _decode_ip(hexstr, ipv6):
    if not ipv6:
        return ".".join(str(int(hexstr[i:i + 2], 16)) for i in (6, 4, 2, 0))
    # /proc/net/tcp6 每 32 位按小端存放，逐字反转后交给 inet_ntop
    raw = b"".join(bytes.fromhex(hexstr[i:i + 8])[::-1]
                   for i in range(0, 32, 8))
    return socket.inet_ntop(socket.AF_INET6, raw)


def established_remotes(port):
    remotes = set()
    for path, ipv6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            with open(path, encoding="utf-8") as f:
                next(f, None)
                for line in f:
                    fields = line.split()
                    if len(fields) < 4 or fields[3] != "01":
                        continue
                    ip_hex, _, port_hex = fields[2].partition(":")
                    if int(port_hex, 16) != port:
                        continue
                    remotes.add(_decode_ip(ip_hex, ipv6))
        except (OSError, ValueError):
            continue
    return remotes


def mqtt_connected(ips, port):
    if not ips:
        return None
    return bool(established_remotes(port) & set(ips))


def node_snapshot(nodes):
    detail = {name: pids_for(name) for name in nodes}
    return detail, sum(1 for pids in detail.values() if pids)


# --------------------------------------------------------------------- health
def cmd_health(args):
    failures = 0
    active = service_active(args.service)
    log("[%s] %s" % ("PASS" if active else "FAIL", args.service))
    if not active:
        failures += 1
    for name in args.nodes:
        pids = pids_for(name)
        log("[%s] %-22s pid=%s" % ("PASS" if pids else "FAIL", name,
                                   ",".join(map(str, pids)) or "-"))
        if not pids:
            failures += 1
    host, port = mqtt_settings(args.host, args.port)
    if host:
        try:
            connected = mqtt_connected(resolve_ips(host, port), port)
        except OSError as exc:
            connected = None
            log("[WARN] MQTT 解析失败：%s" % exc)
        log("[%s] MQTT %s:%s" % ("PASS" if connected else "FAIL", host, port))
        if not connected:
            failures += 1
    else:
        log("[SKIP] MQTT：未配置 MQTT_HOST")
    return 1 if failures else 0


# ----------------------------------------------------------------------- kill
def cmd_kill(args):
    failures = 0
    for name in args.nodes:
        old = pids_for(name)
        if not old:
            log("[SKIP] %-22s 未运行" % name)
            continue
        pid = old[-1]
        started = time.monotonic()
        log("[....] %-22s kill pid=%s" % (name, pid))
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            log("[FAIL] %-22s kill 失败：%s" % (name, exc))
            failures += 1
            continue

        def recovered():
            current = pids_for(name)
            return pid not in current and bool(current) and (
                args.skip_service or service_active(args.service))

        if wait_for(recovered, args.recover_timeout):
            log("[PASS] %-22s 已恢复 pid=%s（%.1fs）" % (
                name, ",".join(map(str, pids_for(name))),
                time.monotonic() - started))
        else:
            log("[FAIL] %-22s %.0fs 内未恢复" % (name, args.recover_timeout))
            failures += 1
    return 1 if failures else 0


# --------------------------------------------------------------------- camera
def camera_sysfs(video_name):
    """返回 (接口名, unbind 路径, bind 路径)，用于模拟摄像头拔出/插入。"""
    iface = os.path.realpath("/sys/class/video4linux/%s/device" % video_name)
    driver = os.path.realpath(os.path.join(iface, "driver"))
    if not os.path.isdir(driver):
        return None
    return (os.path.basename(iface),
            os.path.join(driver, "unbind"),
            os.path.join(driver, "bind"))


def cmd_camera(args):
    if not os.path.exists(args.device):
        log("[FAIL] 设备不存在：%s" % args.device)
        return 1
    video_name = os.path.basename(os.path.realpath(args.device))
    info = camera_sysfs(video_name)
    if not info:
        log("[FAIL] %s 没有可用的 USB 驱动，无法注入拔插" % args.device)
        return 1
    iface_name, unbind, bind = info
    old = pids_for("usb_cam_node_exe")
    log("[....] %s -> %s（%s），usb_cam pid=%s" % (
        args.device, video_name, iface_name,
        ",".join(map(str, old)) or "-"))

    try:
        with open(unbind, "w", encoding="utf-8") as f:
            f.write(iface_name + "\n")
        log("[....] 已解绑 %s，等待 %.0fs" % (iface_name, args.offline))
        time.sleep(args.offline)
    except OSError as exc:
        log("[FAIL] 解绑失败：%s（需要 root）" % exc)
        return 1
    finally:
        try:
            with open(bind, "w", encoding="utf-8") as f:
                f.write(iface_name + "\n")
            log("[....] 已重新绑定 %s，等待恢复" % iface_name)
        except OSError as exc:
            log("[FAIL] 重新绑定失败：%s" % exc)
            return 1

    def device_back():
        return os.path.exists(args.device) and bool(pids_for("usb_cam_node_exe"))

    if not wait_for(device_back, args.recover_timeout):
        log("[FAIL] %.0fs 内 %s 或 usb_cam 未恢复" % (
            args.recover_timeout, args.device))
        return 1
    current = pids_for("usb_cam_node_exe")
    if old and set(current) & set(old):
        log("[WARN] %s 已恢复，但 usb_cam 未重启（可能未触发掉线）" % args.device)
        return 0
    log("[PASS] %s 已恢复，usb_cam pid=%s" % (
        args.device, ",".join(map(str, current)) or "-"))
    return 0


# -------------------------------------------------------------------- network
def _add_rule(tool, spec):
    return run([tool, "-I", "OUTPUT"] + spec)


def _del_rule(tool, spec):
    return run([tool, "-D", "OUTPUT"] + spec)


def _network_broker(args, host, port, ips):
    rules = []
    try:
        for ip in ips:
            tool = "ip6tables" if ":" in ip else "iptables"
            if not shutil.which(tool):
                log("[FAIL] 缺少 %s，无法注入网络故障" % tool)
                return 1
            spec = ["-d", ip, "-p", "tcp", "--dport", str(port), "-j", "REJECT"]
            result = _add_rule(tool, spec)
            if result.returncode != 0:
                log("[FAIL] %s 添加规则失败：%s" % (
                    tool, result.stderr.strip() or result.returncode))
                return 1
            rules.append((tool, spec))
        log("[....] 已拦截 %s:%s 共 %.0fs，等待 MQTT 重连" % (
            host, port, args.offline))
        time.sleep(args.offline)
    finally:
        for tool, spec in reversed(rules):
            _del_rule(tool, spec)
        if rules:
            log("[....] 已移除拦截规则")

    def reconnected():
        return mqtt_connected(ips, port) and (
            args.skip_service or service_active(args.service))

    if wait_for(reconnected, args.recover_timeout):
        log("[PASS] MQTT 已重连 %s:%s" % (host, port))
        return 0
    log("[FAIL] %.0fs 内 MQTT 未重连 %s:%s" % (
        args.recover_timeout, host, port))
    return 1


def _network_link(args):
    if os.geteuid() != 0:
        log("[FAIL] --mode link 需要 root")
        return 1
    if not args.iface:
        log("[FAIL] --mode link 需要 --iface")
        return 1
    # 独立进程兜底：即使 SSH 断开，也会在 offline+10s 后把网卡拉起
    guard = subprocess.Popen(
        ["bash", "-c", "sleep %d; ip link set dev %s up" % (
            args.offline + 10, args.iface)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        result = run(["ip", "link", "set", "dev", args.iface, "down"])
        if result.returncode != 0:
            log("[FAIL] 断开 %s 失败：%s" % (
                args.iface, result.stderr.strip()))
            return 1
        log("[....] %s 已断开，等待 %.0fs" % (args.iface, args.offline))
        time.sleep(args.offline)
    finally:
        run(["ip", "link", "set", "dev", args.iface, "up"])
        guard.kill()

    if not wait_for(lambda: "UP" in run(
            ["ip", "-o", "link", "show", "dev", args.iface]).stdout,
            args.recover_timeout):
        log("[FAIL] %.0fs 内 %s 未恢复 UP" % (
            args.recover_timeout, args.iface))
        return 1
    host, port = mqtt_settings(args.host, args.port)
    if host:
        if not wait_for(lambda: mqtt_connected(resolve_ips(host, port), port),
                        args.recover_timeout):
            log("[FAIL] %s 已恢复，但 MQTT 未重连" % args.iface)
            return 1
    log("[PASS] %s 已恢复 UP" % args.iface)
    return 0


def cmd_network(args):
    if args.mode == "link":
        return _network_link(args)
    host, port = mqtt_settings(args.host, args.port)
    if not host:
        log("[FAIL] 未配置 MQTT_HOST，无法执行 broker 模式")
        return 1
    try:
        ips = resolve_ips(host, port)
    except OSError as exc:
        log("[FAIL] 解析 %s 失败：%s" % (host, exc))
        return 1
    log("[....] Broker %s:%s -> %s" % (host, port, ", ".join(ips)))
    return _network_broker(args, host, port, ips)


# ----------------------------------------------------------------------- soak
def rss_kb(pid):
    try:
        with open("/proc/%d/status" % pid, encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


def fd_count(pid):
    try:
        return len(os.listdir("/proc/%d/fd" % pid))
    except OSError:
        return 0


def dir_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def collect_sample(service, nodes, log_dir):
    detail, running = node_snapshot(nodes)
    all_pids = [pid for pids in detail.values() for pid in pids]
    ffmpeg = pids_for("ffmpeg")
    all_pids += ffmpeg
    try:
        disk_free_mb = round(shutil.disk_usage("/").free / (1024 * 1024), 1)
    except OSError:
        disk_free_mb = None
    return {
        "timestamp": round(time.time(), 3),
        "time": now_str(),
        "service_active": service_active(service),
        "nrestarts": service_restarts(service),
        "node_count": running,
        "nodes": " ".join("%s:%s" % (
            name, ",".join(map(str, pids)) or "-")
            for name, pids in detail.items()),
        "rss_kb": sum(rss_kb(pid) for pid in all_pids),
        "fd_count": sum(fd_count(pid) for pid in all_pids),
        "ffmpeg_count": len(ffmpeg),
        "disk_free_mb": disk_free_mb,
        "log_mb": round(dir_bytes(log_dir) / (1024 * 1024), 1),
        "temp_c": cpu_temp(),
    }


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return 0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def analyze_soak(rows, args):
    if len(rows) < 4:
        log("[WARN] 样本不足（%d），无法判断泄漏趋势" % len(rows))
        return 0
    window = max(3, min(10, len(rows) // 10))
    head, tail = rows[:window], rows[-window:]
    rss_head = _median([r["rss_kb"] for r in head])
    rss_tail = _median([r["rss_kb"] for r in tail])
    fd_head = _median([r["fd_count"] for r in head])
    fd_tail = _median([r["fd_count"] for r in tail])
    rss_growth = (rss_tail - rss_head) / 1024.0
    fd_growth = fd_tail - fd_head
    disk_head = [r["disk_free_mb"] for r in head if r["disk_free_mb"] is not None]
    disk_tail = [r["disk_free_mb"] for r in tail if r["disk_free_mb"] is not None]
    disk_drop = (max(disk_head) - min(disk_tail)) if disk_head and disk_tail else 0
    failures = 0

    def report(label, value, limit, unit):
        nonlocal failures
        ok = value <= limit
        log("[%s] %s %.1f%s（上限 %.1f%s）" % (
            "PASS" if ok else "FAIL", label, value, unit, limit, unit))
        if not ok:
            failures += 1

    report("RSS 增长", rss_growth, args.max_rss_growth_mb, "MB")
    report("FD 增长", fd_growth, args.max_fd_growth, "个")
    report("磁盘占用增长", disk_drop, args.max_disk_drop_mb, "MB")

    service_samples = [r for r in rows if r["service_active"]]
    if rows and rows[0]["service_active"]:
        inactive = len(rows) - len(service_samples)
        log("[%s] 服务存活 %d/%d 个采样" % (
            "PASS" if inactive == 0 else "FAIL",
            len(service_samples), len(rows)))
        failures += 1 if inactive else 0
    else:
        log("[SKIP] 服务非 systemd 启动，跳过存活判定")

    if args.require_stream:
        missing = sum(1 for r in rows if r["ffmpeg_count"] == 0)
        log("[%s] 推流进程存在 %d/%d 个采样" % (
            "PASS" if missing == 0 else "FAIL",
            len(rows) - missing, len(rows)))
        failures += 1 if missing else 0

    first_restart = next((r["nrestarts"] for r in rows
                          if r["nrestarts"] is not None), None)
    last_restart = next((r["nrestarts"] for r in reversed(rows)
                         if r["nrestarts"] is not None), None)
    if first_restart is not None and last_restart is not None:
        log("[%s] systemd 重启次数增加 %d" % (
            "PASS" if last_restart == first_restart else "WARN",
            last_restart - first_restart))
    return 1 if failures else 0


def cmd_soak(args):
    out_dir = os.path.expanduser(args.out_dir)
    log_dir = os.path.expanduser(args.log_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "soak-%s.csv" % time.strftime("%Y%m%d-%H%M%S"))
    total = int(args.hours * 3600)
    deadline = time.monotonic() + total
    rows = []
    log("[....] 采样 %d 个节点、%.2f 小时，输出 %s" % (
        len(args.nodes), args.hours, path))
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SOAK_FIELDS)
            writer.writeheader()
            while time.monotonic() < deadline:
                sample = collect_sample(args.service, args.nodes, log_dir)
                writer.writerow(sample)
                f.flush()
                rows.append(sample)
                left = max(0, int(deadline - time.monotonic()))
                log("[%s] rss=%dKB fd=%d stream=%d disk=%.1fMB left=%ds" % (
                    sample["time"], sample["rss_kb"], sample["fd_count"],
                    sample["ffmpeg_count"], sample["disk_free_mb"] or 0, left))
                time.sleep(max(1, min(args.interval,
                                      deadline - time.monotonic())))
    except KeyboardInterrupt:
        log("[WARN] 手动中断，按已采集样本分析")
    log("[....] 共 %d 个样本，开始分析" % len(rows))
    return analyze_soak(rows, args)


# ----------------------------------------------------------------------- main
def build_parser():
    parser = argparse.ArgumentParser(
        description="P1-5.3 故障注入与长稳测试（设备本机执行）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_nodes(p):
        p.add_argument("--nodes", nargs="+", default=list(DEFAULT_NODES),
                       help="按命令行匹配的节点名/关键字")
        p.add_argument("--service", default=DEFAULT_SERVICE)

    health = sub.add_parser("health", help="检查服务、关键节点与 MQTT 连接")
    add_nodes(health)
    health.add_argument("--host")
    health.add_argument("--port", type=int)

    kill = sub.add_parser("kill", help="逐个 SIGKILL 节点并等待自动恢复")
    add_nodes(kill)
    kill.add_argument("--recover-timeout", type=float, default=30.0)
    kill.add_argument("--skip-service", action="store_true",
                      help="开发环境手动启动时跳过 systemd 判定")

    camera = sub.add_parser("camera", help="USB 断开/重连模拟拔插摄像头")
    camera.add_argument("--device", default="/dev/walle-camera")
    camera.add_argument("--offline", type=float, default=5.0)
    camera.add_argument("--recover-timeout", type=float, default=60.0)

    network = sub.add_parser("network", help="断开 Broker 流量或整块网卡")
    network.add_argument("--mode", choices=("broker", "link"), default="broker")
    network.add_argument("--iface", help="--mode link 时为网卡名，如 wlan0")
    network.add_argument("--offline", type=float, default=15.0)
    network.add_argument("--recover-timeout", type=float, default=180.0)
    network.add_argument("--host")
    network.add_argument("--port", type=int)
    network.add_argument("--service", default=DEFAULT_SERVICE)
    network.add_argument("--skip-service", action="store_true")

    soak = sub.add_parser("soak", help="长稳资源采样与泄漏分析")
    add_nodes(soak)
    soak.add_argument("--hours", type=float, default=24.0)
    soak.add_argument("--interval", type=float, default=10.0)
    soak.add_argument("--out-dir", default="~/.ros/log/stability")
    soak.add_argument("--log-dir", default="~/.ros/log")
    soak.add_argument("--require-stream", action="store_true",
                      help="要求全程存在 ffmpeg 推流进程")
    soak.add_argument("--max-rss-growth-mb", type=float, default=100.0)
    soak.add_argument("--max-fd-growth", type=float, default=50.0)
    soak.add_argument("--max-disk-drop-mb", type=float, default=500.0)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "health": cmd_health,
        "kill": cmd_kill,
        "camera": cmd_camera,
        "network": cmd_network,
        "soak": cmd_soak,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
