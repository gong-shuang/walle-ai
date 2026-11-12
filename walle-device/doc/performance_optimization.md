# 视频链路性能优化记录

> 平台：Orange Pi 5 Plus（RK3588，Ubuntu 22.04，kernel 6.1.43-rockchip）
> 摄像头：PSI USB Camera（MJPG，最高 3264x2448@15 / 1920x1080@30 / 1280x720@30）
> 日期：2026-11-12

## 1. 优化前架构

```text
USB Camera (MJPG)
  → usb_cam 节点（软件 MJPG 解码）
  → /image_raw（RGB，30fps，约 27MB/s）
  → video_webrtc_bridge（Python，拷贝每帧到子进程管道）
  → ffmpeg / gst-launch（RGB→NV12 + libx264 / mpph264enc）
  → RTMP → Oryx/SRS → App WHEP
```

问题：

- usb_cam 软件解 MJPG；
- ROS 原始 RGB 帧序列化/反序列化；
- Python 再拷贝一份 RGB 并写入子进程 stdin；
- 子进程内再做 RGB→NV12；
- 硬件编码只占很小一部分，CPU 主要消耗在拷贝和色彩转换上。

实测（推流中，5 秒采样）：

| 进程 | 占单核 |
| --- | --- |
| gst-launch（推流子进程） | 49.6% |
| usb_cam_node_exe | 27.0% |
| video_webrtc_bridge（Python） | 25.2% |
| video 链路合计 | **约 102% 单核** |
| 整机 CPU | **约 21%**（8 核合计） |

## 2. 方案评估

### 2.1 H.265 暂时不可行

- 设备端：RK3588 支持 H.265 硬件编码，`mpph265enc` 实测可用；
- 服务器：当前 Oryx v5.14.26（`ossrs/oryx:5`，SRS 5）不支持 H.265，需要 SRS 6+；
- App：`stream-webrtc-android:1.3.8`（标准 libwebrtc）不支持 H.265，需要自定义 WebRTC 或更换播放链路；
- 结论：H.265 端到端需要设备、服务器、App 三层同时改造，暂不采用，继续使用 H.264。

### 2.2 “差分协议”不需要

H.264/H.265 本身已使用 I/P/B 帧做帧间差分与预测；额外加一层差分协议不会进一步压缩，反而破坏标准 RTMP/WebRTC 兼容性。

### 2.3 硬件编码器选择

| 方案 | 结果 |
| --- | --- |
| ffmpeg `h264_v4l2m2m` | RK3588 报 `Could not find a valid device`（编码器走 `/dev/mpp_service`，不是 V4L2 M2M 节点） |
| ffmpeg `hevc_v4l2m2m` | 同上 |
| GStreamer `mpph264enc` | 可用，硬件编码 |
| GStreamer `mppjpegdec` | 可用，硬件 JPEG 解码 |

因此设备端统一使用 GStreamer + Rockchip MPP。

## 3. 优化过程

### 3.1 第一步：MPP 硬件编码（方案 A）

- `video_encoder=auto` 优先探测 `mpph264enc`，其次 `h264_v4l2m2m`，最后回退 `libx264`；
- 保持原 usb_cam + `/image_raw` + 子进程推流架构。

结果：编码器本身 CPU 明显下降，但整机变化很小。

| 640x480@30 | 占单核 |
| --- | --- |
| libx264 ultrafast | 约 60%–73% |
| MPP（RGB 管道） | 约 37%–40% |
| MPP（直接 NV12） | 约 12% |

原因：编码只占视频链路的一小部分，usb_cam 软解、ROS 拷贝、Python 拷贝和 RGB→NV12 仍然存在。

### 3.2 第二步：单进程摄像头所有权（方案 B）

`video_webrtc_bridge` 新增 `camera_owner` 模式：

```text
v4l2src(MJPG)
  → jpegparse → mppjpegdec format=NV12
  → tee
      ├─ identity 抽样（约 10fps）→ videoconvert RGB → /image_raw
      └─ mpph264enc → h264parse → flvmux → rtmpsink
```

关键修复与结论：

1. `mppjpegdec` 必须显式 `format=NV12`：
   - 不指定时与 `mpph264enc` 的 DMABuf 格式协商不一致，出现
     `mpp_buffer_create failed`、`hal_h264ve_... wdg timeout`；
   - 指定 NV12 后连续推流无 MPP buffer / watchdog 错误。
2. 不能在 30fps 下做 `NV12→RGB`：
   - 单次转换约 70% 单核；
   - 用 `identity drop-probability=0.6667` 在转换前抽样到 10fps，CPU 明显下降；
   - 不使用 `videorate`，避免其持有 DMABuf 导致编码器 buffer 竞争。
3. 实测推流时：

| 指标 | 结果 |
| --- | --- |
| owner 进程（含摄像头、编码、ROS 图像） | 约 88.8% 单核 |
| 整机 CPU | 约 15.9% |
| MPP 错误 | 0（连续 1 分钟） |

### 3.3 第三步：分离进程 + 共享内存 H.264（最终方案）

为了同时满足“职责分离”和“低拷贝/低延迟”，拆成两个进程：

```text
walle_camera（独占摄像头）
  MJPG → mppjpegdec format=NV12 → tee
    ├─ 10fps → RGB → /image_raw
    └─ mpph264enc → h264parse
         → shmsink socket-path=/tmp/walle-video.sock
           wait-for-connection=true

video_webrtc_bridge（只混流/推流）
  shmsrc socket-path=/tmp/walle-video.sock is-live=true
    → h264parse → flvmux → rtmpsink
                    ↑
            pulsesrc → voaacenc
```

关键点：

- `/image_raw` 仍由 `walle_camera` 发布，AI/网页/startup_check 无感；
- 视频只硬件解码一次、硬件编码一次，bridge 不再接触原始图像；
- `shmsink wait-for-connection=true`：没有消费端时只阻塞 H.264 分支，前置 `queue leaky=downstream` 保证 ROS 分支继续出图；
- bridge 收到 `video/start` 后才启动 `shmsrc`，实现按需推流；
- bridge 停止时关闭 `shmsrc`，摄像头与 `/image_raw` 继续运行；
- `shmsink/shmsrc` 为 GStreamer 共享内存传输，不经过 UDP/TCP/物理网卡，减少协议栈和 socket 拷贝；
- `video_gop` 调整为 30：30fps 下关键帧间隔 1s，晚启动/丢包后的首帧等待从最多 2s 降到 1s。

实测（推流中）：

| 指标 | 结果 |
| --- | --- |
| RTMP 接收端 | 640x480 H.264，317 帧 |
| walle_camera | 约 81.3% 单核 |
| bridge + shmsrc | 约 2.8% 单核 |
| 视频链路合计 | **约 84% 单核**（旧方案约 102%） |
| 整机 CPU | **约 15.6%**（旧方案约 21%） |
| MPP 错误 | 0 |

## 4. 架构模式与切换

`bringup.launch.py` 新增 `camera_backend`：

| 模式 | 说明 |
| --- | --- |
| `auto`（默认） | 检测到 `mppjpegdec` 用 `shm`，否则用 `usb_cam` |
| `gst` | `video_webrtc_bridge` 单进程独占摄像头（方案 B） |
| `shm` | `walle_camera` + 共享内存 H.264 + bridge 推流（最终方案） |
| `usb_cam` | 原方案，兼容其他平台 |

```bash
ros2 launch bringup bringup.launch.py camera_backend:=shm
ros2 launch bringup bringup.launch.py camera_backend:=gst
ros2 launch bringup bringup.launch.py camera_backend:=usb_cam
```

相关参数（`video_webrtc_bridge.yaml`）：

- `camera_device`：摄像头节点，默认 `/dev/video0`；
- `camera_width` / `camera_height` / `framerate`：采集与推流参数；
- `ros_image_fps`：`/image_raw` 目标帧率，默认 10；
- `video_bitrate`：默认 1000k（720p 建议 1500k~2500k）；
- `video_gop`：默认 30；
- `shm_socket`：非空时启用共享内存 H.264 输入。

## 5. 结论

1. RK3588 上必须使用 GStreamer + MPP（`mppjpegdec` / `mpph264enc`）；ffmpeg 的 V4L2 M2M 编码器在该平台不可用。
2. 只替换编码器收益有限；真正的瓶颈是“软件 MJPG 解码 + 原始 RGB 多次拷贝 + 多次色彩转换”。
3. 分离式 + 共享内存 H.264 是当前最优解：
   - 视频只解码/编码一次；
   - bridge 只做混流和 RTMP，职责清晰；
   - 视频链路 CPU 从约 102% 单核降到约 84%，整机从约 21% 降到约 15.6%；
   - 共享内存传输减少协议栈开销，GOP 30 降低首帧延迟。
4. H.265 暂不启用：Oryx 5/SRS 5 与 App 侧 WebRTC 均不支持，需要端到端升级后再评估。

## 6. 后续可选优化

- 将 `ros_image_fps` 从 10 降到 5，进一步降低 RGB 转换 CPU（代价是 `/image_raw` 帧率下降）；
- 对 720p/1080p 做画质与 CPU 对比，确认分辨率提升后的码率/CPU 平衡；
- 评估 `ipcpipeline` 替代 `shmsink`，进一步减少事件/时钟同步开销；
- 为共享内存 socket 增加启动清理和权限检查；
- 多摄像头场景下为每个摄像头分配独立的 `shm_socket` 与编码分支；
- 若后续服务器与 App 支持 H.265，再评估 `mpph265enc` 与 SRS 6+ 的端到端链路。

## 7. 相关提交

- `e4c17f2` feat(video): 新增 Rockchip MPP 硬件编码后端
- `de596e3` feat(video): 新增共享内存分离的摄像头推流链路
