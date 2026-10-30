# N7：设备发现 O(N) 广播的解决方案

> 状态：**已实施（App 侧 + 设备端防抖）**　对应技术债 `N7`
> 关联：[MQTT5 评估](mqtt5-assessment.md) —— 这个问题**不需要** MQTT5 就能解决

---

## 1. 问题重述与量化

原实现（`DevicesViewModel.scan()` + `MqttManager.onConnected()`）：

- App **每次连接成功**（含自动重连）广播一次 `walle/{tenant}/discovery/query`；
- 租户内每台设备都答复一条 `discovery/result`；
- App 用 3 秒窗口收集，窗口结束才结算出设备列表；
- **每次切回设备页**（`DevicesFragment.onStart()`）又重复一遍。

设设备数 `N`、手机数 `M`、每台手机每天重连+切页 `K` 次，则每天报文量：

```
每天报文 ≈ M × K × (1 请求 + N 次投递到设备 + N 条应答 + N 次投递到 App)
         = M × K × (3N + 1)
```

N=20、M=3、K=15 时 ≈ **915 条/天**，而且每台设备每次都要被**唤醒**跑一遍
`build_device_info()`（`psutil` 内存/CPU/挂载 + `get_pi_model` + `get_device_serial`）
→ 每天 **300 次**无意义的设备侧采集，Pi 上还会带来可感的 CPU 抖动。

## 2. 关键事实：retained `online` 已经够用了

设备上线时发布的 retained 报文是这样的：

```python
# linux/rpi_monitor.py: build_online_payload()
info = self.build_device_info()          # 完整身份：device_id / uuid / model / serial /
payload = {"device_id": …, "uuid": …, "online": True}   # os / cpu / memory / disk / ip / mac …
```

即 **retained `online` = 完整身份 + 在线状态**，并且：
- 设备启动时 `publish_json(RES_ONLINE, …, retain=True)`；
- 掉线时由 LWT 代发 `online:false`（同样 retained）。

所以 broker 里永远躺着「当前在线设备」的完整清单。App 订阅后 broker 会**立刻回放**这 N 条
（= 一次连接 N 条报文），根本不需要再问一遍。

而 `discovery/query` 的语义从头到尾只有一个用途：**手动刷新某台设备的身份详情**
（设备重启会换 uuid，需要立刻拿到新值）。

## 3. 分层方案与取舍

| 层 | 方案 | 效果 | 成本 | 结论 |
|:--:|---|---|:--:|:--:|
| L1 | **列表改为「在线设备」**（来自 retained `online`），discovery 降级为按需 | 每次连接 `3N+1 → N`；设备零唤醒 | App 0.5 人日 | ✅ **已实施** |
| L2 | 设备端应答**抖动 + 限频**（多台手机同时手动扫描时不回包风暴） | 手动扫描场景不再瞬时灌满 App | 设备 0.3 人日 | ✅ **已实施** |
| L3 | 设备额外 publish retained `…/info`（身份与在线解耦） | 与 `online` 内容重复，还多占 broker 存储 | 1.5 人日 | ❌ 不做（冗余）；3.1.1 无 `Message Expiry`，过期只能靠 `ts` 自己判 |
| L4 | 服务端聚合器（订 `+/online`，维护列表，retained publish `walle/{t}/devices`） | App 侧 O(1)：1 条报文拿全量 | 2–3 人日 + 常驻进程运维 | ⏸ 仅 N > 100 或移动流量敏感时再上 |
| L5 | 升级 MQTT5（响应主题） | 只是把应答**路由**得更干净，**应答条数仍是 N** | 13–19 人日 | ❌ 不解决本问题，见 [mqtt5-assessment.md](mqtt5-assessment.md) |

> 结论：**枚举 N 台设备必然产生 N 条报文**（除非有服务端聚合）。所以优化目标不是把 O(N) 变成 O(1)，而是：
> ① 把 `O(N)` 的系数从「每次重连 × 每次切页 × 每台手机」降到「每次连接一次」；
> ② 把设备侧的 N 次唤醒降到 **0**；
> ③ 列表从「等 3 秒窗口结算」变成「订阅后立即有」。

## 4. 已实施的改动

### App 侧（`ui/DevicesViewModel.java`，重写 ~60 行）

| 变化 | 说明 |
|---|---|
| 列表数据源 | `shownKeys`（discovery 窗口结算结果）→ **`registry` 里 `online == true` 的设备** |
| `MqttManager.onConnected()` | 不再 `requestDeviceInfo()`，只订阅 8 个响应主题 |
| `DevicesFragment.onStart()` | `scan(false)` → `onScreenVisible()`（只重渲染 + 必要时兜底） |
| `scan(manual)` | 保留，仅「下拉刷新 / 点扫描」触发；3 秒只作为**指示器**上限，不再决定列表成员 |
| 兜底 | 连上后 1.5 秒内 `onlineCount()==0`（broker 无 retained、或老固件未发 retained）→ 自动广播一次 discovery |
| 选中项 | 选中设备下线后自动切到第一台在线设备；一台都不在线时**保持原选中**（不调 `selectDevice("")`，避免注册表自动回填导致的反复通知） |

顺带修掉一个旧缺陷：以前「在线但没在 3 秒窗口内答复 discovery」的设备会从列表消失
（设备忙、`build_device_info` 慢、网络抖动都会触发）；现在列表跟着 `online` 状态走，不会再漏。

### 设备端（`linux/rpi_monitor.py`）

```python
DISCOVERY_JITTER_SEC = 0.8        # 应答前随机延迟上限，N 台设备错开回包
DISCOVERY_MIN_INTERVAL_SEC = 2.0  # 同一台设备两次应答的最小间隔
```

- `handle_device_query()` 先看限频（`last > 0` 才比较，避免 `monotonic < 2s` 的刚开机场景误丢第一条查询），
  再 `time.sleep(random.uniform(0, 0.8))` 抖动后才采集+应答。
- `selftest_topics.py` 新增第 9 组断言：首条正常应答、限频窗口内重复查询被忽略、rid 仍回显。

## 5. 效果估算（N=20, M=3, 每天 15 次连接/切页）

| 指标 | 改造前 | 改造后 |
|---|:--:|:--:|
| 每天报文数 | ~915 | **~300**（只有 retained 回放） |
| 设备侧 `build_device_info()` | 300 次/天 | **0 次/天**（仅手动扫描时） |
| 列表出现耗时 | 最长 3 秒（等窗口结算） | 订阅后立即（retained 回放） |
| 多台手机同时刷新 | N×M 瞬时回包 | 抖动 + 限频，最多 N 条且错开 |

## 6. 验收步骤

```bash
# 1) App 冷启动进入「设备」页：应立即看到在线设备（不需要点扫描）
# 2) logcat 里不应再出现每次连接都发的发现请求：
adb logcat -s MqttTrace | grep discovery/query      # 只应在下拉刷新/兜底时出现
# 3) 断网->恢复（自动重连）：列表靠 retained online 恢复，设备端日志不应出现
#    "已响应设备身份查询"（说明没被广播唤醒）
# 4) 下拉刷新：设备端应打印 1 次应答，多台手机同时刷新时时间戳错开
```

## 7. 残余风险

| 风险 | 缓解 |
|---|---|
| Broker 无 retained（持久化关闭/刚重启） | 1.5 秒兜底广播一次 discovery |
| 老固件 `online` 不带完整身份 | 兜底 discovery 的 `applyInfo` 会补全；建议随本版一起升级设备脚本 |
| 设备端抖动/限频需重新部署才生效 | `scp linux/rpi_monitor.py … && systemctl restart rpi-monitor` |
| 设备数继续增长（>100） | 上 L4 聚合器；届时 App 只需订 1 条主题 |
