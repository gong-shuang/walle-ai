## 服务器的部署

1.本文档描述 walle 服务端的完整搭建流程：**云服务器 → 域名 → 备案 → 证书 → Oryx（媒体）→ EMQX（MQTT）→ 用户 / SSL / ACL**，即完成服务器的配置。

2.服务器的部署是先决条件，完成后，再安装和配置android app（文档见 [quick\_start.md](quick_start.md) ）， 之后，在linux设备上（比如树莓派设备）部署 rpi\_monitor 服务（文档见 [README.md](../linux/README.md) ）。

> 已验证环境（本文命令均按此版本核对）
>
> | 组件   | 版本               | 说明                                       |
> | ---- | ---------------- | ---------------------------------------- |
> | Oryx | **v5.14.26**     | 基于 SRS 的一键音视频服务，Docker 镜像 `ossrs/oryx:5` |
> | EMQX | **v6.3.2**       | MQTT Broker，镜像 `emqx/emqx:6.3.2`         |
> | 系统   | Ubuntu 22.04（推荐） | 其他发行版也可，但 Oryx 官方推荐 Ubuntu 20/22         |
>
> 下文用两个占位符，请替换成你的实际值：
> `SERVER_IP` = 云服务器公网 IP，`www.example.com` = 你的域名。

```
┌────────────┐   MQTT/TLS 8883（控制面：发现/状态/指令/开麦）   ┌────────────┐
│  手机 App  │ ◄───────────────────────────────────────────► │    EMQX    │
└─────┬──────┘                                              └─────┬──────┘
      │ WHIP(HTTPS 443) 推手机麦克风音频                            │ MQTT/TLS 8883
      │ WHEP(HTTPS 443) 拉设备摄像头画面                            │
┌─────▼──────┐                                              ┌─────▼──────┐
│  Oryx/SRS  │ ◄──── RTMP 1935（设备推视频） ────               │  树莓派    │
│  媒体服务   │ ◄──── WHEP 443（设备拉手机音频）───               │ rpi_monitor│
└────────────┘      媒体面统一走 UDP 8000（SRTP）              └────────────┘
```

***

## 0. 端口总表（先看这个）

### 0.1 Oryx（媒体服务）

| 宿主端口  | 容器端口  | 协议      | 作用                                                        | 是否对外          |
| ----- | ----- | ------- | --------------------------------------------------------- | ------------- |
| 80    | 2022  | TCP     | Oryx 控制台 / HTTP API。一键 HTTPS 时也用它做证书签发和 301 跳转            | 对外（否则无法签发证书）  |
| 443   | 2443  | TCP     | **HTTPS**：WHEP / WHIP 信令 + 控制台                            | **必须对外**      |
| 1935  | 1935  | TCP     | **RTMP**：设备（ffmpeg）推视频流                                   | **必须对外**      |
| 8000  | 8000  | **UDP** | **WebRTC 媒体**（ICE 候选 / DTLS-SRTP）。WHIP 上行音频、WHEP 下行视频都走这里 | **必须对外（UDP）** |
| 10080 | 10080 | UDP     | SRT 推流（本项目未使用）                                            | 可不映射          |

> 这组映射来自 Oryx 官方 Docker 命令：`-p 80:2022 -p 443:2443 -p 1935:1935 -p 8000:8000/udp -p 10080:10080/udp`。
> 常见故障：只放行了 TCP 8000 而没放行 **UDP 8000**，表现为「信令成功、但没画面/没声音」。

### 0.2 EMQX（MQTT）

| 端口          | 协议  | 作用                        | 是否对外                 |
| ----------- | --- | ------------------------- | -------------------- |
| 1883        | TCP | MQTT 明文                   | 建议**不对外**（凭据会裸奔）     |
| **8883**    | TCP | **MQTT over TLS**         | **必须对外**（App 与设备都用它） |
| 8083        | TCP | MQTT over WebSocket       | 不使用                  |
| 8084        | TCP | MQTT over WebSocket + TLS | 不使用                  |
| 18083       | TCP | EMQX Dashboard            | 仅管理用，建议只对可信 IP 开放    |
| 4370 / 5369 | TCP | 集群 EPMD / RPC             | **绝不能对外**（单机部署不会用到）  |

### 0.3 为什么必须配 HTTPS / SSL 证书

1. **WHEP / WHIP 是基于 HTTP 的信令协议**，承载 SDP、ICE 候选和推流鉴权 `secret`。走明文 HTTP 会被中间人篡改（把媒体指向攻击者）或窃取 token；而且 WebRTC 规范要求安全上下文，App 端也只在 HTTPS 下使用。
2. **WebRTC 媒体本身已经加密**（DTLS-SRTP），但**信令必须由 TLS 保护**，否则加密的媒体可以被“转卖”到错误的接收方。
3. **MQTT 承载设备账号密码和全部控制指令**（开关摄像头、开麦、远程命令），必须用 8883 + TLS，否则同一网络里任何人都能抓到凭据并控制设备。
4. App 与设备端**都用系统 CA 校验服务端证书**（见 `MqttConfig.tls` 与 `WhepClient.java`），所以必须使用**公共 CA 签发的证书、且 SAN 与域名一致**；自签证书会直接握手失败。
5. 同一域名的公共 CA 证书可以给 **Oryx 的 443** 和 **EMQX 的 8883** 复用；也可以各自签发（Oryx 一键 HTTPS 管 443、certbot 管 8883）。只要**域名与 SAN 一致、签发方是公共 CA**，App/设备端的系统 CA 校验都能通过。

***

## 1. 购买云服务器

1. 选 2 核 2G 起步（Oryx 在 1 路 640×480\@30 + 1 路 Opus 下很轻，2C2G 足够；转码/多路再升）。
2. 系统选 **Ubuntu 22.04**。
3. 在云厂商控制台的**安全组**里按 §0 放行：`80/tcp、443/tcp、1935/tcp、8000/udp、8883/tcp`；`18083/tcp` 建议加 IP 白名单。

验证（本机终端）：

```bash
ssh root@SERVER_IP 'uname -a && cat /etc/os-release | head -2'
ssh root@SERVER_IP 'ss -lntup | head'        # 当前监听端口，应为空或只有 sshd
```

安装 Docker（Oryx / EMQX 都用它）：

```bash
curl -fsSL https://get.docker.com | sh
docker version && docker info | head -5      # 能输出版本与 Server 信息即成功
```

验证安全组是否真的放开（在**本机**执行，不是服务器）：

```bash
nc -zv SERVER_IP 80
nc -zv SERVER_IP 443
nc -zv SERVER_IP 1935
nc -zv SERVER_IP 8883            # 此时 EMQX 还没起，失败属正常，最后再验
```

***

## 2. 购买域名

1. 在云厂商控制台购买域名（如 `example.com`）。
2. 添加 DNS 解析：`A 记录`，主机记录 `www`（或直接根域名）→ 指向 `SERVER_IP`。
3. 如果用 CDN/加速，关闭回源缓存，否则 1935/8000 这类非 HTTP 端口无法透传。

验证解析生效：

```bash
dig +short A www.example.com         # 应输出 SERVER_IP
# 或
nslookup www.example.com
```

> 提示：没有 `dig` 时先 `apt install -y dnsutils`（或只用 `nslookup`）。

验证从公网可达（服务器已启动 Oryx 后再做，见 §5）：

```bash
curl -sI http://www.example.com/ | head -1
```

***

## 3. 备案 / 域名认证（约 3 个工作日，可与后面步骤并行）

- 云服务器在**中国大陆**时，域名必须完成 **ICP 备案**，否则解析到大陆 IP 的网站会被拦截（表现为浏览器打不开、WHEP 请求超时）。
- 备案在云厂商控制台提交（需要营业执照/身份证、域名证书等），通常 3\~20 个工作日。
- **备案只影响域名对外访问，不影响你先在本机把 Oryx/EMQX 跑起来、做 1935 / 8000 的连通性测试**，所以这一步和 §4\~§10 可以并行。
- 验证：云厂商「备案管理」里状态变为 **已备案 / 审核通过**；之后 `curl -sI http://www.example.com/` 不再被拦截。

***

## 4. 申请免费 SSL 证书

> 这一步的证书主要给 **EMQX 8883** 用（§9）；Oryx 的 443 由 §6「一键 HTTPS」自动签发。
> 两个服务用同一张证书（若 Oryx 版本支持导入）或各自一张同域名证书，效果一样。

### 方式 A：certbot（推荐）

```bash
apt install -y certbot
certbot certonly --standalone -d www.example.com --agree-tos -m you@mail.com --no-eff-email
# 证书位置：
#   /etc/letsencrypt/live/www.example.com/fullchain.pem   （服务器证书链）
#   /etc/letsencrypt/live/www.example.com/privkey.pem     （私钥）
```

> `--standalone` 需要 80 端口空闲。如果 80 已被 Oryx 占用，用 `--webroot -w <网站目录>`，或先停 Oryx 再签。
> Let's Encrypt 证书 90 天有效，`certbot renew` 会自动续期（可加 `--deploy-hook` 重启容器）。

验证证书可用：

```bash
openssl x509 -in /etc/letsencrypt/live/www.example.com/fullchain.pem -noout -subject -issuer -dates
# subject 里应包含 CN 或 SAN = www.example.com，issuer = Let's Encrypt
```

### 方式 B：Oryx 一键 HTTPS（自动申请 + 自动续期）

见 §6：Oryx 自己申请并管理 443 的证书；EMQX 侧仍按方式 A 单独签一张同域名证书即可。

***

## 5. 用 Docker 启动 Oryx

```bash
docker run --restart always -d -it --name oryx \
  -v $HOME/oryx-data:/data \
  -p 80:2022 -p 443:2443 -p 1935:1935 -p 8000:8000/udp -p 10080:10080/udp \
  ossrs/oryx:5
```

- `-v $HOME/oryx-data:/data`：配置、证书、录像都落在这里，容器重建不丢。
- 镜像 `ossrs/oryx:5` 是 v5 大版本的最新补丁；需要固定版本时把 tag 改成具体补丁号（以 Oryx Releases 页面为准）。
- 如果 80/443 已被 Nginx 占用，先停掉，或把宿主端口改成 `-p 2022:2022 -p 2443:2443` 并自行反代（不推荐，一键 HTTPS 会失效）。

验证：

```bash
docker ps --filter name=oryx          # STATUS 应为 Up
curl -sI http://127.0.0.1/ | head -3          # 200/302，说明控制台已起（宿主 80 → 容器 2022）
curl -sI http://www.example.com/ | head -1    # 公网可达（302 跳转到控制台/HTTPS）
nc -zv 127.0.0.1 1935                          # RTMP 端口在监听
ss -lunp | grep 8000                           # WebRTC UDP 在监听
```

> 注意：宿主只发布了 `80 → 2022`、`443 → 2443`，所以本机访问控制台用 `http://127.0.0.1/`（不是 `:2022`）；`2022` 是容器内端口。

启动后浏览器打开 `http://www.example.com/` 进入 Oryx 控制台（首次会要求设置管理密码）。

***

## 6. Oryx 后台一键 HTTPS

Oryx 控制台 → **配置 / 系统 → HTTPS**（不同小版本菜单位置略有差异，找 “HTTPS”“一键 HTTPS”“SSL” 即可）：

1. 填写域名 `www.example.com`（必须与证书/解析一致）。
2. 点击「一键 HTTPS」。Oryx 会向 **Let's Encrypt** 申请证书并配置到 2443（对外 443），证书到期自动续期。
3. 成功后控制台地址变为 `https://www.example.com/`，HTTP 会自动 301 跳转。

验证（关键）：

```bash
# 1) HTTPS 能访问
curl -sI https://www.example.com/ | head -3

# 2) 证书是公共 CA 且域名匹配（不能是自签）
openssl s_client -connect www.example.com:443 -servername www.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# 3) 证书链校验通过（无 verify error）
openssl s_client -connect www.example.com:443 -servername www.example.com </dev/null 2>&1 \
  | grep -E "Verify return code|Verification"
```

> 若报 `certificate verify failed` / `self signed certificate`：说明不是公共 CA 证书，App 的 WHEP 会直接失败。

**推流鉴权（secret）**：Oryx 开启推流鉴权后，推流地址必须带 `secret=` 参数；不带会直接返回 502（已实测）。这个 secret 每台服务器不同，配置方法见 §6.1。

WHEP/WHIP 端点（本项目使用的固定路径）：

```
拉流（WHEP，无需 secret）：https://www.example.com/rtc/v1/whep/?app=live&stream={room}
推音频（WHIP，必须带 secret）：https://www.example.com/rtc/v1/whip/?app=live&stream={room}_audio&secret=<推流token>
推视频（RTMP，必须带 secret）：rtmp://www.example.com:1935/live/{room}?secret=<推流token>
```

**不要用非法 SDP 探测 RTC 接口**：用 `--data 'v=0'` 这类残缺 SDP 打 WHEP/WHIP，Oryx 会因解析失败返回 **502**，这并不代表服务异常（已实测）。真正的接口验证放到 §11：实际推/拉一路流，看控制台是否出现 stream。

```bash
# 只确认「路由存在、没有被 404」：对 WHEP/WHIP 返回 502 属正常（后端拒绝非法 SDP）
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/sdp' --data 'v=0' \
  'https://www.example.com/rtc/v1/whep/?app=live&stream=probe'
```

### 6.1 Oryx 推流 secret（每台服务器不同，必须单独配置）

Oryx 部署完成后会生成一个 **32 位十六进制的推流 secret**（例如 `a2616d64…`），它**不是通用的**：

- 每台服务器各自生成；重装 Oryx、或控制台里「重置」后，**旧 secret 立即失效**；
- 所有**推流**动作都要带它：设备推视频（RTMP）、手机推音频（WHIP）；
- **拉流（WHEP）不需要** secret。

> 因此代码里**不写死** secret：App 在构建期注入、设备端在运行时从环境变量读取，部署到新服务器只改配置、不改代码。

**在哪里获取**：

1. Oryx 控制台 → **Streams / 媒体** 列表，点开任意一路流查看推流地址，`secret=` 后面的值即为此；
2. 或 Oryx 控制台的 **系统设置 → 推流鉴权 / Token / Secret** 页面（不同小版本叫法不同）；
3. 部署时如果已经用旧版本填过，也可以从旧配置里读取（设备端 `/etc/rpi-monitor.env` 或 App 的构建参数）。

**怎么配置**：

| 端                | 配置位置                   | 写法                                                                                |
| ---------------- | ---------------------- | --------------------------------------------------------------------------------- |
| 树莓派              | `/etc/rpi-monitor.env` | `ORYX_SECRET=<secret>`                                                            |
| 树莓派（可选，覆盖整条 URL） | `/etc/rpi-monitor.env` | `VIDEO_RTMP_URL_TEMPLATE=rtmp://www.example.com:1935/live/{room}?secret={secret}` |
| App              | `gradle.properties`    | `oryxSecret=<secret>`                                                             |
| App              | 命令行构建                  | `./gradlew :app:assembleDebug -PoryxSecret=<secret>`                              |
| App              | 环境变量                   | `ORYX_SECRET=<secret> ./gradlew :app:assembleDebug`                               |

**验证**：

```bash
# 设备端：--check 会显示 secret 是否已配置（不会打印明文）
./.venv/bin/python rpi_monitor.py --check | grep 'Oryx secret'
# 期望：Oryx secret: 已配置

# App：点麦克风按钮，不应再提示「未配置 Oryx 推流 secret」；
# 成功时 adb logcat 里能看到 WHIP 返回 201
adb logcat -s WhipAudio:V WhepDemo:I
```

**安全提示**：secret 泄露（截图、日志、仓库）后，在 Oryx 控制台重置，然后同步更新 App 构建参数与设备端 `ORYX_SECRET` 并重启服务；日志里的推流地址已由 `mask_rtmp_url()` 做 `secret=***` 脱敏。

***

## 7. 用 Docker 启动 EMQX

```bash
docker run -d --name emqx --restart always \
  -v $HOME/emqx-data:/opt/emqx/data \
  -p 1883:1883 -p 8883:8883 -p 8083:8083 -p 8084:8084 -p 18083:18083 \
  emqx/emqx:6.3.2
```

- 挂载 `/opt/emqx/data`：用户、ACL、监听器配置等 mnesia 数据在容器重建后保留。
- 生产环境不要暴露 1883 / 8083 / 8084，只留 8883 + 内网 18083。

验证：

```bash
docker ps --filter name=emqx                 # STATUS 应为 Up (healthy)
curl -sI http://127.0.0.1:18083/ | head -1   # 200
```

浏览器打开 `http://SERVER_IP:18083`（或经 SSH 隧道 `ssh -L 18083:127.0.0.1:18083 root@SERVER_IP`），默认账号 **admin / public**，**首次登录会强制改密码**。

验证 MQTT 端口（`ssl:default` 默认会用 EMQX 内置自签证书监听 8883，此时能连通但 App 会因证书校验失败）：

```bash
nc -zv SERVER_IP 8883
```

***

## 8. 在 EMQX Web 创建用户（树莓派 + App）

Dashboard → **访问控制 → 认证（Authentication）** → 创建认证器：

1. 类型选 **Password-Based / 内置数据库（Built-in Database）**。
2. 算法默认 `sha256`（或 `bcrypt`）即可。
3. 创建两个用户：

| 用户名       | 用途        | 说明                             |
| --------- | --------- | ------------------------------ |
| `home`    | 手机 App 登录 | **它同时是租户名**；请改成 App 实际登录的用户名   |
| `RaspPi5` | 树莓派设备账号   | 每台设备一个账号更安全；名字随意，需与 ACL 里的正则一致 |

> `walle/{tenant}/{device}/...` 里，`tenant` = App 的用户名，`device` = 设备的 `device_id`（16 位十六进制，由 `rpi_monitor.py` 生成）。
> 两边不一致时 App 收不到设备，这是最常见的“设备在线但列表空”的原因。

验证（先不加密，验证账号可用）：

```bash
apt install -y mosquitto-clients

# 用设备账号发布到自己的子树（把 <clientid> 换成设备 device_id，必须与 -i 一致）
mosquitto_pub -h SERVER_IP -p 1883 -u RaspPi5 -P '设备密码' -i '<clientid>' \
  -t 'walle/home/<clientid>/online' -m '{"online":true}' -d

# 用 App 账号订阅租户整棵子树，应能收到上面的消息
mosquitto_sub -h SERVER_IP -p 1883 -u home -P 'App密码' -t 'walle/home/#' -v -C 1
```

Dashboard → **监控 → 客户端（Clients）** 也应能看到两个连接，`clientid` 与用户名对应。

***

## 9. 给 8883 端口配置 SSL 证书

Dashboard → **管理 → 监听器（Listeners）** → 找到 **`ssl:default`（8883）** → 编辑 → **SSL 配置**：

- **证书（Cert）**：`fullchain.pem`（服务器证书 + 中间证书）
- **私钥（Key）**：`privkey.pem`
- **CA 证书**：可选（仅当需要校验客户端证书时；公共 CA 场景留空即可）
- 保存后监听器会自动重载（个别版本需重启容器）

> 直接用 §4 方式 A 的 certbot 证书上传这两个文件即可；Oryx 的 443 证书由它自己管理，不需要在这里复用。

验证：

```bash
# 1) TLS 握手成功 + 证书信息（issuer 应为公共 CA，CN/SAN 为域名）
openssl s_client -connect www.example.com:8883 -servername www.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# 2) 用设备账号走 TLS 发布（--tls-use-os-certs 用系统 CA 校验，模拟 App/设备端行为）
mosquitto_pub -h www.example.com -p 8883 --tls-use-os-certs \
  -u RaspPi5 -P '设备密码' -i '<clientid>' \
  -t 'walle/home/<clientid>/online' -m '{"online":true}' -d
```

若 `--tls-use-os-certs` 不被 mosquitto 版本支持，改成：

```bash
mosquitto_pub -h www.example.com -p 8883 --cafile /etc/ssl/certs/ca-certificates.crt \
  -u RaspPi5 -P '设备密码' -i '<clientid>' -t 'walle/home/<clientid>/online' -m '{}' -d
```

> App 端默认就是 **8883 + 系统 CA**（`app/build.gradle` 的 `MQTT_DEFAULT_PORT=8883`），设备端在 `/etc/rpi-monitor.env` 里设 `MQTT_PORT=8883`（`MQTT_TLS=auto` 会自动开启）。
> 证书不能是自签或证书域名与填写的主机名不一致，否则两端都会连接失败。

***

## 10. 在 EMQX Web 配置 ACL 规则

Dashboard → **访问控制 → 授权（Authorization）** → 添加 **File** 授权源，把下面内容放到文件**最前面**（并把它排到授权源顺序的第一位，EMQX 按顺序匹配、第一条命中即生效）：

```erlang
%% 手机端：读写本租户整棵子树
{allow, {username, {re, "^home$"}}, all, ["walle/home/#"]}.

%% 设备端：只读写自己的子树，并能收到租户内的发现广播
{allow, {username, {re, "^RaspPi5$"}}, publish,   ["walle/home/${clientid}/#"]}.
{allow, {username, {re, "^RaspPi5$"}}, subscribe, ["walle/home/${clientid}/#",
                                                   "walle/home/discovery/query"]}.
```

规则说明：

| 规则                           | 含义                                          |
| ---------------------------- | ------------------------------------------- |
| `{username, {re, "^home$"}}` | 匹配 App 账号（正则全匹配）；`all` = 既可发布也可订阅           |
| `walle/home/#`               | 该租户的整棵子树（`#` 只能出现在末尾）                       |
| `${clientid}`                | 由客户端连接时的 `clientid` 动态替换；设备账号只能碰自己这一支       |
| `publish` / `subscribe`      | 分别控制发布与订阅；设备端订阅 `discovery/query` 才能收到租户内广播 |
| `{allow, ...}`               | 第一条命中即生效，后面的规则不再看                           |

> - 把 `home` / `RaspPi5` 换成你的实际账号；App 端用户名即租户名。
> - 设备连上来时 `clientid` 必须等于 `device_id`（`rpi_monitor.py` 里已用 `device_id` 作为 MQTT client\_id），否则 `${clientid}` 展开后与主题对不上，表现为发布被拒。
> - 如需收紧为「未命中即拒绝」，在文件末尾追加：`{deny, all}.`
> - 保留 EMQX 自带的 `$SYS` 规则，不要覆盖删除；改完保存即热加载，无需重启。

验证（分别验证「允许」与「拒绝」）：

```bash
# 允许：App 账号订阅整棵子树
mosquitto_sub -h www.example.com -p 8883 --tls-use-os-certs -u home -P 'App密码' \
  -t 'walle/home/#' -v -d

# 允许：设备账号发布自己的 online
mosquitto_pub -h www.example.com -p 8883 --tls-use-os-certs -u RaspPi5 -P '设备密码' \
  -i '<clientid>' -t 'walle/home/<clientid>/online' -m '{"online":true}' -d

# 拒绝：设备账号越权订阅别人的子树（应无 SUBACK / 被断开，或收不到任何消息）
mosquitto_sub -h www.example.com -p 8883 --tls-use-os-certs -u RaspPi5 -P '设备密码' \
  -i '<clientid>' -t 'walle/other/#' -v -d
```

第三条命令如果**能收到消息**，说明 ACL 没生效（检查是否把 File 授权源排在第一位、用户名正则是否与实际账号一致）。

***

## 11. 服务端验收清单

在部署设备/App 前，逐条打勾：

| #  | 检查项        | 命令 / 位置                                                                     | 期望                            |
| -- | ---------- | --------------------------------------------------------------------------- | ----------------------------- |
| 1  | 域名解析       | `dig +short A www.example.com`                                              | 返回 `SERVER_IP`                |
| 2  | Oryx 在跑    | `docker ps --filter name=oryx`                                              | `Up`                          |
| 3  | HTTPS 与证书  | `openssl s_client -connect www.example.com:443 -servername www.example.com` | `Verify return code: 0`       |
| 4  | RTMP 端口    | `nc -zv www.example.com 1935`                                               | succeeded                     |
| 5  | WebRTC UDP | 服务器 `ss -lunp \| grep 8000`，安全组放行 8000/udp                                  | 已监听、已放行                       |
| 6  | RTC 接口     | 用设备/App 实际推一路流，Oryx 控制台能看到 `live/{room}` / `live/{room}_audio`              | 流出现在列表、`docker logs oryx` 无报错 |
| 7  | EMQX 在跑    | `docker ps --filter name=emqx`                                              | `Up (healthy)`                |
| 8  | 8883 TLS   | §9 的 openssl / mosquitto                                                    | 握手成功、issuer 为公共 CA            |
| 9  | 用户         | Dashboard → 认证 → 用户管理                                                       | `home`、`RaspPi5` 存在           |
| 10 | ACL        | §10 的三条验证                                                                   | 允许通过、越权被拒                     |
| 11 | 推流 secret  | 设备 `--check` 显示「Oryx secret: 已配置」；App 构建时已带 `-PoryxSecret`                  | 两端与 Oryx 控制台的值一致（§6.1）        |

服务端就绪后，把下面这些值填到两端：

| 配置项                             | 位置                                          | 值                                                                                       |
| ------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------- |
| `ORYX_SECRET`（App）              | `gradle.properties` / `-PoryxSecret` / 环境变量 | Oryx 推流 secret（§6.1）                                                                    |
| `WHIP_AUDIO_URL_TEMPLATE`       | `app/build.gradle`（由 ORYX\_SECRET 自动拼装）     | `https://www.example.com:443/rtc/v1/whip/?app=live&stream={room}_audio&secret=<secret>` |
| `WHEP_URL_TEMPLATE`             | `app/build.gradle`                          | `https://www.example.com:443/rtc/v1/whep/?app=live&stream={room}`                       |
| `ORYX_SECRET`（设备端）              | `/etc/rpi-monitor.env`                      | 与 App 用同一个 secret（§6.1）                                                                 |
| `VIDEO_RTMP_URL_TEMPLATE`       | `linux/rpi_monitor.py`（由 ORYX\_SECRET 自动拼装） | `rtmp://www.example.com:1935/live/{room}?secret=<secret>`                               |
| `PHONE_AUDIO_WHEP_URL_TEMPLATE` | `linux/rpi_monitor.py` / 环境变量               | `https://www.example.com:443/rtc/v1/whep/?app=live&stream={stream}`                     |
| `MQTT_HOST` / `MQTT_PORT`       | `/etc/rpi-monitor.env`                      | `www.example.com` / `8883`                                                              |
| MQTT 主机 / 端口                    | App 内 MQTT 配置页                              | `www.example.com` / `8883`（TLS 开）                                                       |

***

## 12. 常见故障对照

| 现象                                     | 原因                                         | 排查                                         |
| -------------------------------------- | ------------------------------------------ | ------------------------------------------ |
| 浏览器打不开域名，但 `curl http://SERVER_IP/` 正常 | 域名未备案 / DNS 未生效                            | §3、`dig`                                   |
| WHEP 拉流或 WHIP 推流实际失败                   | `secret` 缺失/错误、后端异常（注意：用非法 SDP 探测本身也会 502） | 检查 `ORYX_SECRET`、`docker logs oryx`、App 日志 |
| WHIP 推流 502、WHEP 正常                    | 构建 App 时没带 `-PoryxSecret`，或值不对             | §6.1                                       |
| 点麦克风提示「未配置 Oryx 推流 secret」             | App 构建期没注入 secret                          | 用 `-PoryxSecret=...` 重新构建（§6.1）            |
| 视频推流返回「未配置 Oryx 推流 secret」             | 设备端 `/etc/rpi-monitor.env` 缺 `ORYX_SECRET` | 补上后 `sudo systemctl restart rpi-monitor`   |
| 有信令、无画面/无声音                            | **UDP 8000 没放行**                           | 安全组 / `ss -lunp`                           |
| App 报 WHEP 证书校验失败                      | 自签证书或 SAN 不匹配                              | §6 的 openssl 校验                            |
| App 连不上 MQTT                           | 8883 未配证书 / 端口没开 / 密码错                     | §9、`openssl s_client`                      |
| 设备在线但 App 列表为空                         | 租户（App 用户名）与设备 `MQTT_TENANT` 不一致           | 对照 `walle/{tenant}/{device}`               |
| 设备能连但发布被拒                              | ACL 正则与账号不符，或 `clientid` 不等于 `device_id`   | §10                                        |
| 手机说话延迟大                                | 音频走了旧 RTMP 路径，或声卡是高延迟设备                    | 升级到 WHIP/WHEP 版本，检查 `PHONE_AUDIO_DEVICE`   |

