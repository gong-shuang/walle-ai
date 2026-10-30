> **⚠️ 历史归档**：这是协议 v1 时期的需求文档，其中的主题名（`device/online`、`cmd/exec` 等）
> 与代码结构**均已过时**。当前实现见 [`../README.md`](../README.md) 与 [`../rpi_monitor.py`](../rpi_monitor.py)。
> 不需要的话可以直接删除本目录。

---

================================================================================
树莓派系统监控 Agent —— 功能需求清单与实现说明
================================================================================
项目目录 : /Coze/Drive/RaspberryPi_mqtt_test/
主程序   : rpi_monitor.py        （单文件，约 1730 行）
文档     : README.md             （报文示例、部署、运维细节）
测试     : test_video.py / test_capability.py / test_device_id.py
本文件   : 按需求提出顺序逐条列出「要什么 → 怎么实现 → 报文长什么样 → 怎么验证」
整理时间 : 2026-10-30

注意：本文件是「功能需求清单」，不是 pip 依赖清单。
      运行依赖见文末【附录 A】。


================================================================================
一、功能总览清单
================================================================================
编号   功能                                    主题（订阅 / 发布）                        状态
--------------------------------------------------------------------------------
F01    系统状态异常告警                        → alarm/device                             已实现
F02    设备详细状态查询                        device/detail/query → device/detail/result 已实现
F03    远程命令执行                            cmd/exec → cmd/result                      已实现
F04    设备身份信息查询（免 uuid）             online/device/query → online/device/result 已实现
F05    设备标识 device_id + 运行实例 uuid      （贯穿所有报文）                            已实现
F06    上线 / 下线通知（含异常掉线遗嘱）       → device/online                            已实现
F07    摄像头采集能力查询                      capability/query → capability/result        已实现
F08    请求 ffmpeg 推流                        video/request/send → video/request/result  已实现
F09    停止推流                                video/stop/send → video/stop/result        已实现
F10    主题命名两轮调整                        （6 个主题改名）                            已实现
F11    身份报文瘦身（删 3 个字段）             online/device/result                       已实现
F12    推流幂等判定（同参数不重推、异参数重推） video/request/send                        已实现
F13    broker 改域名 + 推流日志落盘            配置项                                     已实现
F14    ffmpeg 完整日志（含版本/流信息/帧统计）  log/ffmpeg_{time}.log                      已实现
F15    日志无限增长治理（自动滚动）             log/ffmpeg_{time}.log.1                    已实现
F16    device_id 硬件指纹（md5 16 位）          （贯穿所有报文 + client_id）                已实现

订阅 6 个主题、发布 8 个主题；所有订阅主题统一走 on_message → 线程池分发。


================================================================================
二、逐项需求与实现说明
================================================================================

--------------------------------------------------------------------------------
F01  系统状态异常告警  →  alarm/device
--------------------------------------------------------------------------------
【需求】
  监控系统状态，出现异常立即上报 alarm/device。需覆盖：
  ① USB 设备连接 / 断开
  ② CPU 使用率 ≥ 90%
  ③ 内存使用率 ≥ 90%
  ④ 网络带宽 ≥ 10MB
  ⑤ 硬盘使用率 ≥ 90%
  报文「尽量详细」。

【实现】
  1) 主循环  RpiMonitor.monitor_loop()          （rpi_monitor.py:1603）
     每 MONITOR_INTERVAL=5 秒一轮，循环内依次做：
       - 采样：psutil.cpu_percent(percpu=True) / virtual_memory / disk_mounts
               / get_cpu_temp / calc_net_rates
       - 阈值判定：check_threshold()  × N 次
       - USB 热插拔：check_usb()
     首次进入循环先给 cpu_percent 打一次点，避免第一轮读到 0。

  2) 阈值判定  RpiMonitor.check_threshold()     （:885）
     统一入口，参数：key（去重标识）、value、threshold、metric、alarm_type、unit、label、extra。
     - value >= threshold 且 key 不在 active_alarms 中  → 发 alarm，并登记
     - value <  threshold * RECOVER_RATIO(0.9) 且 key 在 active_alarms 中 → 发 recover，并移除
     0.9 回滞的作用：在阈值附近抖动时不会反复「告警-恢复-告警」刷屏。

  3) USB 热插拔  RpiMonitor.check_usb()         （:906）
     用 scan_usb_devices() 扫描 /sys/bus/usb/devices，得到 {设备键: 设备信息}。
     与上一轮快照 self.usb_state 做集合差：
       新增 → event=usb_connect，level=info
       移除 → event=usb_disconnect，level=warning
     程序启动时先把当前 USB 列表作为基线（self.usb_state 在 __init__ 初始化），
     所以「开机时已经插着的设备」不会产生历史告警。

  4) 报文封装  RpiMonitor._event()              （:863）
     所有告警/恢复/插拔都走同一个封装，保证字段一致：
       device_id / uuid / hostname / ip / event / level / metric /
       alarm_type / message / timestamp / datetime / detail

  5) 覆盖的告警类型（alarm_type）
     cpu_high              CPU 使用率 ≥ 90%
     memory_high           内存使用率 ≥ 90%
     temperature_high      CPU 温度 ≥ 80℃        （附加项，树莓派过热保护用）
     disk_high             每个挂载点单独判定，key = disk_high:/mount
     network_rx_high       入站速率 ≥ 10 MiB/s
     network_tx_high       出站速率 ≥ 10 MiB/s
     usb_connect / usb_disconnect

【配置项】
  MONITOR_INTERVAL = 5
  THRESH_CPU = 90.0 / THRESH_MEM = 90.0 / THRESH_DISK = 90.0 / THRESH_TEMP = 80.0
  THRESH_NET_BPS = 10 * 1024 * 1024
  RECOVER_RATIO = 0.9

【报文示例】
  {
    "device_id": "41b45dda8b5e4815",
    "uuid": "c004cb13-0004-5252-aa84-4b09f6517c5e",
    "hostname": "raspberrypi",
    "ip": "192.168.1.50",
    "event": "alarm",
    "level": "warning",
    "metric": "cpu",
    "alarm_type": "cpu_high",
    "message": "CPU 使用率当前 93.2%，已达到/超过阈值 90%",
    "timestamp": 1793260800,
    "datetime": "2026-10-29 16:00:00",
    "detail": {"value": 93.2, "unit": "%", "threshold": 90.0,
               "per_core_percent": [95.0, 91.0, 92.5, 94.0]}
  }

【验证】
  真实链路：临时把 THRESH_CPU 调到 1 观察 alarm → recover 成对出现；
  USB：插拔 U 盘，收到 usb_connect / usb_disconnect 各一条。

--------------------------------------------------------------------------------
F02  设备详细状态查询  device/detail/query  →  device/detail/result
--------------------------------------------------------------------------------
【需求】
  订阅状态查询主题，收到请求后上报设备实时状态（报文尽量详细）。

【实现】
  订阅 TOPIC_QUERY_STATUS，回调 RpiMonitor.handle_query_status()  （:1492）
    ① verify_uuid() 校验 uuid（不通过静默忽略）
    ② build_status() 现场采集一次（:1369）
    ③ publish_json(TOPIC_MACHINE_STATUS, status)

  build_status() 先调用 sample_once(1.0)：在 1 秒窗口内做两次打点算真实 CPU 与网络速率，
  比直接读 psutil 的瞬时值准确（网络速率必须有时间差才能算）。

  报文结构（顶层 11 个字段，下挂 6 个子对象）：
    device_id / uuid / hostname / ip / timestamp / datetime / uptime_seconds
    system  : os、kernel、os_version、python、machine、model
    cpu     : percent、per_core_percent[]、count_logical/physical、
              load_avg_1m/5m/15m、frequency_mhz{current,min,max}、
              temperature_c、sample_interval_sec
    memory  : percent、total/used/available/free_bytes + human、
              swap{percent,total,used,human}
    disk    : mounts[]（每个挂载点：device、mount、fstype、percent、
              total/used/free_bytes + human）
    network : sample_interval_sec、total_rx/tx_bytes_per_sec、
              total_rx/tx_rate、interfaces[]（每网卡速率与累计字节）
    usb     : count、devices[]
    alarms  : active[]（当前未恢复的告警 key）、thresholds{}（阈值快照）

【验证】
  mosquitto_pub 发 {"uuid":"<最新uuid>"} 到 device/detail/query，
  mosquitto_sub 收到 device/detail/result，字段齐全；uuid 错误时无任何回复。

--------------------------------------------------------------------------------
F03  远程命令执行  cmd/exec  →  cmd/result
--------------------------------------------------------------------------------
【需求】
  订阅 cmd/exec 执行 shell 命令，把 stdout/stderr/退出码回 cmd/result。

【实现】
  RpiMonitor.handle_cmd_exec()  （:1505）
    ① verify_uuid() 校验
    ② ENABLE_CMD_EXEC=False → 直接回 status=error 说明已禁用
    ③ 校验 cmd 字段非空字符串
    ④ CMD_ALLOWLIST 非空时按前缀匹配，不在白名单 → status=denied（不执行）
    ⑤ timeout 取请求值，夹在 1 ~ CMD_TIMEOUT_MAX(300) 秒之间，默认 30
    ⑥ subprocess.run(shell=True, capture_output=True, text=True, timeout=…)
    ⑦ 输出超过 CMD_OUTPUT_LIMIT(64KB) 用 _truncate() 截断并标记
    ⑧ 回传：request_id、cmd、status(ok/error/timeout/denied)、exit_code、
            stdout、stderr、duration_ms

  request_id：请求里带了就沿用（服务端可做请求响应配对），没带则本机 uuid4 前 8 位。
  命令在线程池里跑（max_workers=4），长命令不会堵住 MQTT 收发线程。

【配置项】
  ENABLE_CMD_EXEC = True
  CMD_TIMEOUT_DEFAULT = 30 / CMD_TIMEOUT_MAX = 300 / CMD_OUTPUT_LIMIT = 64KB
  CMD_ALLOWLIST = []        # 空 = 允许任意命令；生产建议填前缀白名单

【安全提醒】
  shell=True 意味着可以执行任意命令，务必保证 broker 侧只有可信客户端，
  并建议把 CMD_ALLOWLIST 打开。

--------------------------------------------------------------------------------
F04  设备身份信息查询  online/device/query  →  online/device/result
--------------------------------------------------------------------------------
【需求】
  新增设备身份查询，用于在一堆嵌入式设备里区分「这是哪台」。字段含型号、主机名、
  IP、系统版本、内核版本、运行时间等。

【实现】
  订阅 TOPIC_DEVICE_QUERY，回调 RpiMonitor.handle_device_query()  （:995）
  该主题**不校验 uuid**（按需求指定：任何客户端都能查询身份），直接 build_device_info() 回包。

  build_device_info()  （:924）采集 21 个字段：
    device_id / uuid / device_type / vendor / model / serial / hostname / ip / mac
    uuid_source{mac,ip,note}
    os{name,kernel,kernel_build,arch,python}
    cpu{model,cores_logical,cores_physical,frequency_mhz,temperature_c}
    memory_total_bytes / memory_total_human
    disk_total_bytes / disk_total_human
    uptime_seconds / uptime_human / boot_time / timestamp / datetime

  型号来源 get_pi_model()：读 /proc/device-tree/model（树莓派/OrangePi 都适用）。
  序列号 get_device_serial()：/proc/device-tree/serial-number，取不到则 None。
  系统版本 get_os_pretty_name()：/etc/os-release 的 PRETTY_NAME。

【说明】
  uuid 是「运行实例标识」，重启会变；服务端要拿最新 uuid，就调这个主题（免校验）。

--------------------------------------------------------------------------------
F05  设备标识：device_id + uuid，以及报文校验
--------------------------------------------------------------------------------
【需求（分两次提出）】
  ① 启动时生成网络内唯一的 uuid，除身份查询主题外，其余订阅主题都要校验 uuid；
  ② uuid 只保证「本次运行期间不变、下次运行变化」；
     device_id 才是设备唯一标识 —— 用 MAC + CPU 核心数 + 内存总容量做 md5 取 16 位字符，
     只要不换 CPU / 内存就永远不变。

【实现】
  两个标识都在 RpiMonitor.__init__ 里计算（:780）：
    self.mac        = pick_primary_mac(self.lan_ip)      # 优先取默认路由网卡的 MAC
    self.cpu_cores  = get_cpu_core_count()               # 物理核优先，取不到用逻辑核
    self.mem_total  = psutil.virtual_memory().total      # 总容量（字节）
    DEVICE_ID       = generate_device_id(mac, cpu_cores, mem_total)
    DEVICE_UUID     = generate_device_uuid(mac, ip)      # uuid5(NAMESPACE_OID, "mac|ip|启动时间")

  generate_device_id()  （:344）
    hashlib.md5(f"{mac}|{cpu_cores}|{mem_total_bytes}".encode()).hexdigest()[:16]
    → 16 位小写十六进制；换网卡 / 换 CPU / 加内存才变，重装系统、换 IP、重启都不变。
    实际取值为 MQTT client_id（取代原来的主机名，避免同主机名设备互相踢下线）。

  generate_device_uuid()  （:354）
    str(uuid.uuid5(uuid.NAMESPACE_OID, f"{mac}|{ip}|{ts}"))
    → 标准 UUID 格式；ts 取启动时刻（秒），所以本次运行期间恒定、下次启动必变。

  校验：RpiMonitor.verify_uuid()  （:1468）
    空报文 / JSON 解析失败 / 非对象 / uuid 缺失 / uuid 不匹配 → 记 warning 日志并静默忽略，
    不执行、不回复（避免被扫描器探活）。

  校验矩阵：
    主题                     是否校验 uuid
    device/detail/query          是
    cmd/exec                     是
    capability/query             是
    video/request/send           是
    video/stop/send              是
    online/device/query          否（身份查询免校验，按需求指定）

【待确认（未改动）】
  曾讨论过把身份查询改成「不带 uuid 放行、带 uuid 但错误则忽略」的中间方案，
  该方案未得到确认，因此保持现状（完全免校验）。

--------------------------------------------------------------------------------
F06  上线 / 下线通知  →  device/online
--------------------------------------------------------------------------------
【需求】
  ① 设备上线、下线时发布通知；
  ② 主题 device/online 的 JSON 内容要和 online/device/result 保持一致。

【实现】
  三者共用同一份 build_online_payload(online: bool)  （:981）：
    info = build_device_info()
    payload = {device_id, uuid, online} + info 其余字段   → 共 22 个字段
  区别只在 online 布尔值，字段名/层级与 online/device/result 完全一致。

  三个发布点：
    1) on_connect() 连接成功   → online=true,  retain=True   （:809）
    2) will_set() 遗嘱         → online=false, retain=True   （:1658，连接前设定）
    3) run() finally 优雅退出  → online=false, retain=True   （:1658）

  遗嘱的意义：进程被 kill -9、断电、断网等非正常退出时，broker 自动代发 offline，
  服务端不会一直显示「在线」。
  代价：遗嘱必须在 connect 之前定好，内容静态，所以遗嘱里的实时指标是启动那一刻的快照；
  online 字段和 device_id / uuid / hostname / ip 是准确的，足够判断在线状态。

  retain=True 的作用：服务端/App 后上线也能立刻拿到设备最后状态。

【报文示例】
  {"device_id":"41b45dda8b5e4815","uuid":"…","online":false,
   "device_type":"raspberrypi","model":"Raspberry Pi 5 Model B Rev 1.0",…共 22 字段}

--------------------------------------------------------------------------------
F07  摄像头采集能力查询  capability/query  →  capability/result
--------------------------------------------------------------------------------
【需求】
  新增能力查询主题（要校验 uuid），返回本机摄像头采集能力列表。

【实现】
  订阅 TOPIC_CAPABILITY_QUERY，回调 handle_capability_query()  （:1041）
    ① verify_uuid()
    ② build_capability()（:1007）→ 发布 TOPIC_CAPABILITY_RESULT

  探测链路：
    list_cameras()  （:553）
      → _v4l2(["--list-devices"]) 执行 v4l2-ctl，超时 CAM_CMD_TIMEOUT=8s
      → _parse_v4l2_devices()  解析出 [{name, detail, nodes[]}]
           v4l2 输出格式为 `卡片名: 驱动 (总线):`，按第一个冒号切分：
           name=卡片名（如 USB 2.0 Camera），detail=冒号后内容（如 USB Camera (usb-xhci…)）
      → 每个 /dev/videoN 再执行 `-d <node> --list-formats-ext`
      → _parse_formats() 解析：
           [0]: 'MJPG' (Motion-JPEG, compressed)     → format / description / flags
           Size: Discrete 1920x1080                  → resolutions[]
           Interval: Discrete 0.033s (30.000 fps)    → fps[]
      → 汇总每个摄像头的 format_count / resolution_count

  降级策略（不会因为没装工具就报错）：
    - CAM_ENABLE=False                → cameras=[], probe.error="摄像头能力探测已关闭"
    - 没装 v4l2-ctl / 命令不存在      → cameras=[], probe.error 说明原因
    - 单个节点探测失败                → 该节点跳过，不影响其他节点

  报文结构：
    device_id / uuid / hostname / ip / capability="camera"
    capabilities[]  : ["camera_video_capture","rtmp_push","system_status","remote_cmd"]
    camera_count    : 摄像头数量
    cameras[]       : 每项 {name, detail, device(/dev/videoN), formats:[{format,
                       description, flags, resolutions:[{width,height,fps[]}], …}],
                       format_count, resolution_count}
    summary         : {total_cameras, total_formats, total_resolutions, probe_cost_ms}
    probe           : {tool:"v4l2-ctl", error}   error=None 表示探测正常
    timestamp / datetime

  依赖：sudo apt install -y v4l-utils

--------------------------------------------------------------------------------
F08  请求 ffmpeg 推流  video/request/send  →  video/request/result
--------------------------------------------------------------------------------
【需求】
  订阅 video/request/send（要校验 uuid），JSON 含 uuid / video_device / room_number。
    - 设备不存在  → result = -1，cause = "没有此摄像头设备"
    - 设备存在    → 启动 ffmpeg 推 RTMP，命令里设备用 video_device、
                    livestream 换成 room_number，**必须动态拼接，不能写死**
    - 成功        → result = 0，cause = "ok"
    - 失败        → result = -2，cause = "命令执行失败"
  结果统一发 video/request/result。

【实现】
  handle_video_request()  （:1207）判定顺序（顺序很重要，决定错误码）：
    ① verify_uuid
    ② VIDEO_ENABLE=False            → -2（推流能力已关闭）
    ③ 设备校验：正则 ^/dev/video\d+$ 且 os.path.exists → 否则 -1
    ④ 房间号白名单：VIDEO_ROOM_PATTERN ^[A-Za-z0-9_-]{1,64}$ → 否则 -2
       （房间号要拼进 URL，必须严格校验，防注入）
    ⑤ 幂等判定（见 F12）
    ⑥ 启动推流 _start_video_stream()  （:1145）
    ⑦ 回包 _publish_video_result()   （:1059）

  build_ffmpeg_cmd()  （:618）—— 参数全部来自配置/请求，动态拼接：
    ffmpeg -loglevel info
           -f v4l2 -thread_queue_size 8192
           -video_size 640x480 -framerate 30
           -i <video_device>
           -c:v libx264 -preset ultrafast -tune zerolatency
           -pix_fmt yuv420p -g 60 -b:v 1000k
           -an -f flv <rtmp_url>
    · 用列表传参、不走 shell，设备名/房间号无法注入命令
    · 不加 -hide_banner：保留 ffmpeg 版本/编译选项/流信息，便于排查
    · rtmp_url 由 build_rtmp_url() 用模板 + 房间号拼出（F13）

  启动成功判定：_start_video_stream 里起进程后等 VIDEO_STARTUP_GRACE=3 秒，
  这期间进程还活着 → 认为推流成功（result=0）；3 秒内退出 → 视为失败（result=-2，
  并把日志路径和最后一条错误行带回 error 字段）。

  进程管理：
    · start_new_session=True，独立进程组，方便整组终止
    · stdout=PIPE + stderr=STDOUT + bufsize=0，交给泵线程落盘（F14）
    · 同时注册看护线程 _video_watch()（:1126），进程异常退出时清状态并记日志
    · 同一时刻只保留一路推流（self.video_proc / self.video_lock）

  报文附加字段：
    action           : started / restarted / already_streaming / failed
    rtmp_url         : 已脱敏（secret 显示为 ***）
    command          : 完整命令（已脱敏），便于服务端核对
    pid / started_at / startup_grace_sec
    previous_room_number / previous_video_device（重推时）
    error            : 失败原因（含日志路径、ffmpeg 最后一条错误行）

--------------------------------------------------------------------------------
F09  停止推流  video/stop/send  →  video/stop/result
--------------------------------------------------------------------------------
【需求】
  订阅 video/stop/send（要校验 uuid），停止 ffmpeg：
    - 当前没有设备在推流 → result = -1，cause = "当前没有设备在推流"
    - 停止成功            → result = 0，cause = "ok"

【实现】
  handle_video_stop()  （:1313）
    ① verify_uuid
    ② 先取当前推流状态（meta 停止后会被清空，必须提前留副本）
    ③ 没有在跑的进程 → -1
    ④ 请求里带了 room_number 且与当前推流房间号不一致 → -1（防止停错流）
    ⑤ _stop_video_stream()  （:1081）：SIGTERM → 等 3 秒 → 超时 SIGKILL
    ⑥ 回包 _publish_video_stop_result()  （:1293）

  主动停止 vs 异常退出：
    停止前给进程打 proc._intentional_stop = True，看护线程 _video_watch 据此
    只记 info 日志，不会误报「ffmpeg 异常退出」。

  回包字段：result / cause / room_number / video_device / pid / started_at / stopped

--------------------------------------------------------------------------------
F10  主题命名调整（两轮，共 6 个）
--------------------------------------------------------------------------------
【需求】
  第一轮（4 个）：query/status → device/detail/query、machine/status → device/detail/result、
                  device/query → device/online/query、device/info → device/online/result
  第二轮（2 个）：device/online/query → online/device/query、
                  device/online/result → online/device/result

【实现】
  全部收敛到配置区常量，代码里不再出现硬编码主题字符串：
    TOPIC_ALARM           = "alarm/device"
    TOPIC_QUERY_STATUS    = "device/detail/query"
    TOPIC_MACHINE_STATUS  = "device/detail/result"
    TOPIC_CMD_EXEC        = "cmd/exec"
    TOPIC_CMD_RESULT      = "cmd/result"
    TOPIC_ONLINE          = "device/online"
    TOPIC_DEVICE_QUERY    = "online/device/query"
    TOPIC_DEVICE_INFO     = "online/device/result"
    TOPIC_CAPABILITY_QUERY  = "capability/query"
    TOPIC_CAPABILITY_RESULT = "capability/result"
    TOPIC_VIDEO_REQUEST     = "video/request/send"
    TOPIC_VIDEO_RESULT      = "video/request/result"
    TOPIC_VIDEO_STOP        = "video/stop/send"
    TOPIC_VIDEO_STOP_RESULT = "video/stop/result"
  改名时同步更新：on_connect 的订阅列表、on_message 的分发分支、README、测试断言。

--------------------------------------------------------------------------------
F11  身份报文瘦身：删除 3 个字段
--------------------------------------------------------------------------------
【需求】
  online/device/result 报文删除 interfaces、mqtt、monitor 三个字段，「直接删除即可」。

【实现】
  build_device_info() 不再返回这三个字段（字段数 24 → 21）。
  影响面检查：
    · build_online_payload 复用 build_device_info，device/online 同步瘦身（22 字段）
    · README 示例报文同步删除
    · 测试断言字段数改为 21 / 22

--------------------------------------------------------------------------------
F12  推流幂等判定：同参数不重推、异参数重推
--------------------------------------------------------------------------------
【需求】
  收到 video/request/send 时先判断当前是否有 ffmpeg 在推流：
    - 在推流且 video_device、room_number 都相同 → 不处理
    - 有任意不同 → 断掉之前的流，用新参数重新推流

【实现】
  handle_video_request() 第 ⑤ 步：
    streaming = proc 存在且 proc.poll() is None
    if streaming and meta.video_device == video_device and meta.room_number == room_number:
        → action = "already_streaming"，result = 0，附 pid / started_at / uptime_sec
          （明确说明「相同参数已在推流，本次不做处理」，不重启、不断流）
    else:
        previous_room / previous_device 先留存
        _stop_video_stream("收到新的推流请求")
        _start_video_stream(...)
        → action = "restarted"（有旧流）或 "started"（原来没流）

  为什么需要：App 反复下发同一条推流请求（重连、页面刷新、定时轮询）时，
  不应该每次都断流重连，否则画面会一直卡在关键帧等待。

  测试覆盖 [5.1] 共 15 项：同参不重启、换房间重推、换设备重推、旧进程已死按新请求处理、
  action 取值、previous_* 字段、uptime_sec 计算等。

--------------------------------------------------------------------------------
F13  broker 改域名 + 推流日志落盘
--------------------------------------------------------------------------------
【需求】
  ① 把 IP 换成域名 www.baidong.xyz；
  ② ffmpeg 推流命令把日志输出到文件；
  ③ 日志放在工作目录下的 log 文件夹，文件名 ffmpeg_{time}.log，
     {time} 替换成当前时间，每次推流文件名都不同。

【实现】
  ① 域名
     MQTT_HOST = "www.baidong.xyz"（MQTT 1883 连接）
     VIDEO_RTMP_URL_TEMPLATE =
       "rtmp://www.baidong.xyz:1935/live/{room}?secret=a2616d641f344f8386c349f874ff2e4a"
     全项目已无硬编码 IP。

  ② ③ 日志文件
     VIDEO_LOG_FILE = "log/ffmpeg_{time}.log"      （相对进程工作目录）
     video_log_path()  （:648）
       time.strftime("%Y%m%d_%H%M%S") 替换 {time} → log/ffmpeg_20261029_162854.log
       目录不存在时 mkdir -p 自动创建
     每次 _start_video_stream 都调用一次 video_log_path()，
     所以每次推流生成一个独立文件，不会互相覆盖。
     文件开头写分隔头：时间 + room + device + 脱敏后的完整命令，便于事后对照。

  RTMP URL 脱敏：mask_rtmp_url()  （:772）把 secret=xxx 换成 secret=***，
  回包和日志里都不暴露推流密钥。

--------------------------------------------------------------------------------
F14  ffmpeg 完整日志（版本 / 流信息 / 帧统计）
--------------------------------------------------------------------------------
【需求】
  希望日志文件里记录截图那样的完整 ffmpeg 输出：
  版本、configuration、libav* 组件、Input #0、Stream mapping、libx264 参数、
  Output #0、frame= 统计。

【实现】
  1) 日志级别
     VIDEO_LOG_LEVEL = "info"（可配 quiet/panic/fatal/error/warning/info/verbose/debug）
     命令里**去掉 -hide_banner**，这样 banner、Input/Output、Stream mapping 都会输出。

  2) 关键难点：帧统计用 \r 原地刷新
     ffmpeg 的 `frame= 120 fps= 30 ...` 是靠 \r 回到行首反复刷新的，整段可能长时间没有 \n。
     如果按行读（readline），要么读不到、要么等到下一次换行才落盘，日志看起来像卡住。
     解决：Popen(stdout=PIPE, stderr=STDOUT, bufsize=0) + 独立泵线程
           _log_pump()  （:1104）用 os.read(fd, 4096) **按块读**，读到就写，不等换行。

  3) 落盘：_LogSink（:654）
     · 线程安全（加锁），泵线程写、主线程读，互不干扰
     · 内存里保留最后 8KB（keep_tail），用于失败时提取原因
     · 写盘后 flush，保证 tail -f 能实时看到
     · 文件不可写（权限/磁盘满）时降级到 tempfile，并在回包 error 里说明

  4) 失败原因提取：_LogSink.error_line()  （:735）
     info 级别下日志被 banner 淹没，简单截尾会返回 "0 / 58.134.100 ..." 这类版本行。
     改为：把尾部 8KB 按 \r 和 \n 切成行，优先挑包含 error/cannot/invalid/no such/
     failed/denied/busy/refused/unable/unsupported 等关键词的最后一行。
     实测能准确返回 "/dev/video0: No such file or directory"。

  5) 回包里的 error 字段带上日志路径，例如：
     "ffmpeg 退出码 1（日志: log/ffmpeg_20261029_162854.log）: /dev/video0: No such file or directory"

【日志文件长这样】
  ===== 2026-10-29 16:28:54 启动推流 room=123456 device=/dev/video0 =====
  $ ffmpeg -loglevel info -f v4l2 ... rtmp://www.baidong.xyz:1935/live/123456?secret=***
  ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021 the FFmpeg developers
    built with gcc 11 (Ubuntu 11.2.0-19ubuntu1)
    configuration: --prefix=/usr ...
    libavutil 56. 70.100 / 56. 70.100
    ...
  Input #0, video4linux2,v4l2, from '/dev/video0':
    Duration: N/A, start: 1234.567, bitrate: 147456 kb/s
    Stream #0:0: Video: rawvideo (YUYV ...), yuyv422, 640x480, 30 fps
  Stream mapping:
    Stream #0:0 -> #0:0 (rawvideo (native) -> h264 (libx264))
  [libx264 @ 0x...] using cpu capabilities: ARMv8 NEON
  [libx264 @ 0x...] profile High, level 3.0
  Output #0, flv, to 'rtmp://www.baidong.xyz:1935/live/123456?secret=***':
    ...
  frame=  120 fps= 30 q=23.0 size=     512kB time=00:00:04.00 bitrate=1048.6kbits/s speed=1.00x

--------------------------------------------------------------------------------
F15  日志无限增长治理（自动滚动）
--------------------------------------------------------------------------------
【需求（由「这个日志文件会不断的变大」引申）】
  单次推流可能持续几小时，info 级别下帧统计每 0.5 秒刷一次，日志会一直涨。

【实现】
  VIDEO_LOG_MAX_BYTES = 20 * 1024 * 1024      # 单文件上限 20MB
  _LogSink.write() 里**先判断再写**：
      if size >= max_bytes: self._rotate()      # 当前文件 → 名字 + ".1"（覆盖旧的 .1）
      self.fh.write(data); flush(); size += len(data)
  先判断后写的意义：触发滚动的那条日志一定完整落在某一个文件里，不会被劈成两半。
  每次推流本身又是一个新文件，所以长期运行最多占用 2 × 20MB / 次推流。
  需要更省空间：把 VIDEO_LOG_LEVEL 调成 "warning" 或 "error"。

--------------------------------------------------------------------------------
F16  device_id 硬件指纹
--------------------------------------------------------------------------------
（与 F05 同一实现，单列是因为它是独立提出的一条需求）
  规则：MAC 地址 + CPU 核心数 + 内存总容量 → md5 → 取前 16 位字符
  代码：generate_device_id()（:344）+ get_cpu_core_count()（:333）
  实测：41b45dda8b5e4815，两次启动相同；同时 uuid 每次不同。
  用途：MQTT client_id（避免 clientId 冲突互踢）、所有报文里的设备唯一标识。


================================================================================
三、主题总表（共 14 个）
================================================================================
方向    主题                        用途                                   校验uuid  QoS
--------------------------------------------------------------------------------
订阅    device/detail/query         设备详细状态查询                         是        1
订阅    cmd/exec                    远程命令执行                             是        1
订阅    online/device/query         设备身份信息查询                         否        1
订阅    capability/query            摄像头采集能力查询                       是        1
订阅    video/request/send          请求推流                                 是        1
订阅    video/stop/send             停止推流                                 是        1
发布    device/detail/result        状态查询结果                             -         1
发布    cmd/result                  命令执行结果                             -         1
发布    online/device/result        设备身份信息                             -         1
发布    capability/result           能力查询结果                             -         1
发布    video/request/result        推流结果（-1/0/-2）                      -         1
发布    video/stop/result           停止结果（-1/0）                         -         1
发布    alarm/device                告警/恢复/USB 插拔                       -         1
发布    device/online               上下线（retain + 遗嘱）                  -         1

  订阅消息统一处理链：on_message → 线程池(4 worker) → handle_xxx → verify_uuid → 回包


================================================================================
四、配置项清单（rpi_monitor.py 顶部配置区）
================================================================================
【MQTT】
  MQTT_HOST = "www.baidong.xyz"      MQTT_PORT = 1883
  MQTT_USER = "RaspPi5"              MQTT_PASSWORD = "123456"
  MQTT_KEEPALIVE = 60                MQTT_QOS = 1

【监控阈值】
  MONITOR_INTERVAL = 5               THRESH_CPU = 90.0
  THRESH_MEM = 90.0                  THRESH_DISK = 90.0
  THRESH_TEMP = 80.0                 THRESH_NET_BPS = 10 * 1024 * 1024
  RECOVER_RATIO = 0.9

【远程命令】
  ENABLE_CMD_EXEC = True             CMD_TIMEOUT_DEFAULT = 30
  CMD_TIMEOUT_MAX = 300              CMD_OUTPUT_LIMIT = 64 * 1024
  CMD_ALLOWLIST = []

【摄像头能力】
  CAM_ENABLE = True                  CAM_CMD_TIMEOUT = 8

【视频推流】
  VIDEO_ENABLE = True                VIDEO_FFMPEG_BIN = "ffmpeg"
  VIDEO_RTMP_URL_TEMPLATE = "rtmp://www.baidong.xyz:1935/live/{room}?secret=…"
  VIDEO_VIDEO_SIZE = "640x480"       VIDEO_FRAMERATE = 30
  VIDEO_BITRATE = "1000k"            VIDEO_GOP = 60
  VIDEO_STARTUP_GRACE = 3.0          VIDEO_ROOM_PATTERN = ^[A-Za-z0-9_-]{1,64}$
  VIDEO_LOG_FILE = "log/ffmpeg_{time}.log"
  VIDEO_LOG_LEVEL = "info"
  VIDEO_LOG_MAX_BYTES = 20 * 1024 * 1024


================================================================================
五、测试清单
================================================================================
文件                  项数   覆盖内容
--------------------------------------------------------------------------------
test_video.py          100    ffmpeg 命令拼接、设备不存在 -1、房间号非法 -2、uuid 校验、
                              推流成功、换房间/换设备重推、秒退 -2、ffmpeg 未安装、
                              停止推流全套、[5.1] 重复请求判定 15 项、
                              [5.2] 日志落文件（目录/时间戳/分隔头/脱敏/stdout=PIPE/
                              \r 实时落盘/日志级别/滚动 .1/尾部缓存/错误行提取）
test_capability.py      27    无 v4l2-ctl 降级、真实树莓派 v4l2 输出解析、
                              uuid 校验、订阅主题列表
test_device_id.py       19    device_id 生成规则（长度/十六进制/同输入同结果/md5 一致）、
                              换 MAC/核心数/内存则变、与运行环境无关、
                              两次启动 device_id 不变 uuid 变、报文双标识

运行方式：
  pip install -q "paho-mqtt>=2.0" psutil
  python test_device_id.py && python test_capability.py && python test_video.py


================================================================================
六、已知限制 / 待确认
================================================================================
1) online/device/query 完全免校验（按需求指定）。曾提议「带 uuid 但错误则忽略」的
   中间方案，未确认，保持现状。
2) device/online 的遗嘱内容在 connect 前固定，异常掉线时其中的实时指标是启动快照；
   online 布尔值、device_id、uuid、hostname、ip 准确。
3) 沙箱环境无 /dev/video*、无 v4l2-ctl，推流链路用真实 ffmpeg 的失败分支验证；
   真机（树莓派 / OrangePi + USB 摄像头）需实跑一次确认画面与日志内容。
4) 日志分隔头里的 `$ ffmpeg ...` 一行是否保留，尚未确认（若不需要，可去掉该行）。
5) 当前只有一路推流能力，多摄像头同时推流需要扩展为进程字典。


================================================================================
【附录 A】运行依赖（pip）
================================================================================
  pip install "paho-mqtt>=2.0" psutil

  # 系统包
  sudo apt install -y ffmpeg v4l-utils

  运行：python rpi_monitor.py
  开机自启：见 rpi-monitor.service（systemd）

================================================================================
