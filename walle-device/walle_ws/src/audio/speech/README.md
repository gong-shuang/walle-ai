# speech 兼容层说明与上机部署指南

> 适用场景：原 `speech.so` 是幻尔（Hiwonder）用 Cython 3.1.0 针对 **Python 3.10 / Ubuntu 22.04 / aarch64** 编译的私有扩展模块。
> 迁移到 Ubuntu 24.04（Python 3.12）后无法加载，本仓库用纯 Python 重写同名同 API 的兼容层。

---

## 一、问题结论（背景）

在 Python 3.12 下直接加载原 `speech.so`，实测报错：

```
ImportError: speech.so: undefined symbol: _PyUnicode_Ready
```

`rpi5/speech.so`（3.11 编译版）在 3.12 下报同一个错，唤醒模块 `resources/detect_rpi5.so` 也一样。

原因：

1. 它是 Cython 编译的普通扩展（`PyInit_speech`），不是 abi3/稳定 ABI；文件名是裸 `speech.so`，任何 Python 版本都会尝试加载。
2. 代码引用了 CPython 私有 API：`_PyUnicode_Ready`、`_PyUnicodeWriter_*`、`_PyObject_GC_New`、`_PyType_Lookup`、`_PyThreadState_UncheckedGet`、`PyFrame_New` 等，这些在 3.12 已删除或改动。
3. 编译头文件是 `/usr/include/python3.10`。厂商自己就是按 Python 次版本分包：`py3.6 / py3.8 / py3.10 / py3.11`，**没有 py3.12**。

glibc 与架构不是问题（22.04 → 24.04 向前兼容，同为 aarch64），唯一卡点是 Python ABI。

---

## 二、交付物

```
ros2_ws/src/speech/
├── package.xml                    # ament_python 包描述
├── setup.py / setup.cfg
├── resource/speech                # ament 索引标记
├── speech/
│   ├── __init__.py                # from . import speech
│   ├── speech.py                  # 兼容实现主体（约 640 行）
│   └── awake.py                   # 原版纯 Python，原样保留
└── scripts/
    └── speech_selfcheck.py        # 上机自检脚本
```

安装方式二选一：

- **推荐**：作为 colcon 包，`colcon build --packages-select speech` 后 `source install/setup.bash`。
- 直接 pip：`pip3 install src/speech`（同一份 setup.py，可独立安装）。

---

## 三、已验证内容

| 验证项 | 方法 | 结果 |
|---|---|---|
| Python 3.12 可导入 | CPython 3.12.16 aarch64 加载 | 通过 |
| API 签名对齐 | 与原库逐项比对 `inspect.signature` | **44/44 完全一致**，0 差异 |
| 工具函数数值 | 与原库同输入对比 | `normalize → [0,250,-250,8192]`、`int2float` 逐值相同、`downsample_block(48000→16000)` 一致 |
| wav 写入 | `voice_save` 生成文件读回 | 通道/位宽/采样率/帧数正确 |
| LLM 请求结构 | 本地 mock HTTP 服务抓包 | `messages = [system, user]` 正确 |
| VLLM 请求结构 | 同上 | `content = [{type:text},{type:image_url, url:"data:image/jpeg;base64,...", detail:"high"}]` 与原库字面量一致 |
| 异常路径 | 无 key / 无效设备号 | 抛清晰 RuntimeError / 返回 `''`，不崩溃 |
| colcon 构建 | `colcon build --packages-select speech` | 通过，脚本安装到 `install/speech/lib/speech/` |
| 自检脚本 | 本机实跑 | 输出完整报告，逻辑正常 |

未验证（需真机 + 云端 key）：真实麦克风录音链路、dashscope/OpenAI 实际调用、板载音量控件。

---

## 四、与原库的行为差异（均为有意设计）

| 项 | 原库 | 兼容层 |
|---|---|---|
| 中文 ASR | 边录边推 dashscope，有中间结果 | 先 VAD 截断录一段（最长 10s），再按 100ms 帧推 `paraformer-realtime-v2`，返回最终句 |
| 英文 ASR | OpenAI realtime websocket | 走 `audio.transcriptions`（`whisper-1` / `gpt-4o-transcribe`）；`update_session` 参数保存，但只用 model/language/prompt |
| 唤醒 | snowboy `detect_*.so` | 不再需要；`awake.py` 串口唤醒原样可用，`alsa` 模式本来就不唤醒 |
| VAD | Silero `vad.jit` + torch | 统一 `webrtcvad`，不加载 torch（Pi 上省约 1GB 内存和启动时间） |
| `Vad` | Silero 实现 | 内部委托 `webrtcvad`，签名保持原样 |
| `speak_text` | 原库不存在（仓库调用点本来会崩） | 按调用点补了实现 |
| `on_open/on_message/on_error/on_close/websocket_thread` | websocket 回调 | 保留为空实现，仅接口对齐 |

---

## 五、新机器部署步骤

### 1. 拷包

把 `src/speech/` 整个目录放到新机 `~/ros2_ws/src/speech/`（与 `large_models` 同级）。

### 2. 清掉旧包（关键，否则会继续加载坏 .so）

```bash
pip3 uninstall -y speech 2>/dev/null
sudo rm -rf /usr/local/lib/python3.12/dist-packages/speech \
            /usr/local/lib/python3.12/dist-packages/speech-1.0.dist-info \
            ~/.local/lib/python3.12/site-packages/speech*
python3 -c "import speech" ; echo "上面应报 ModuleNotFoundError 才算清干净"
```

### 3. 系统依赖

```bash
sudo apt update
sudo apt install -y build-essential libportaudio2 alsa-utils \
                    python3-opencv python3-numpy python3-scipy python3-serial
```

### 4. Python 依赖（24.04 是 PEP 668 托管环境，要加参数）

```bash
pip3 install --user --break-system-packages sounddevice webrtcvad openai dashscope
```

### 5. 编译并 source

```bash
cd ~/ros2_ws
colcon build --packages-select speech
source install/setup.bash
python3 -c "from speech import speech as s; print(s.__file__)"
```

最后一行必须输出 `install/speech/lib/python3.12/site-packages/speech/speech.py`。
如果输出 `.so`，说明第 2 步没清干净。

### 6. 跑自检

```bash
ros2 run speech speech_selfcheck
ros2 run speech speech_selfcheck --mic-test 3 --device 0
ros2 run speech speech_selfcheck --mic-test 3 --device 1
```

正常结果：环境/包/依赖/设备全 OK，只有密钥项取决于 `config.py` 是否已加载。
`--mic-test` 会录音、报峰值、自动回放，用来确认设备号。

参考：本机（Pi5 / Ubuntu 22.04）实测音频索引

```
0  PSI USB CAMERA: Audio   ch=2   <- 摄像头麦克风
1  pulse                   ch=32  <- PulseAudio 默认源
2  default                 ch=32
```

`vocal_detect` 默认 `mic_device=1`（即 pulse）。若 `--mic-test 1` 录不到声音而 `--device 0` 有声音，就把 launch/param 里的 `mic_device` 改成 0。

### 7. 串口权限（唤醒板必须）

```bash
sudo usermod -aG dialout $USER   # 重新登录生效
ls -l /dev/ttyCH341USB0          # CircleMic 的设备节点
```

### 8. 环境变量 + 启动

```bash
export ASR_LANGUAGE=Chinese      # 建议写进 ~/.bashrc 或 .robotrc
ros2 launch bringup bringup.launch.py
```

`config.py` 第 38 行直接读 `os.environ["ASR_LANGUAGE"]`，没设就 KeyError，这一步不能漏。

---

## 六、常见报错对照

| 现象 | 原因 / 处理 |
|---|---|
| `ros2 run speech: package not found` | 没 `source install/setup.bash` 或没 build |
| `import speech` 报 `undefined symbol: _PyUnicode_Ready` | 旧包没删干净，按第 2 步查 `import speech; print(speech.__file__)` |
| `pip3 install` 报 `externally-managed-environment` | 漏了 `--break-system-packages --user` |
| `webrtcvad` 安装失败 | 缺 gcc，装 `build-essential` |
| `sounddevice` 报找不到 PortAudio | 缺 `libportaudio2` |
| `set_volume` 返回 False | 板子声卡没有 PCM 控件，自检只报 WARN，不影响功能 |
| ASR 一直返回空 | 先 `--mic-test` 确认设备号，再确认 `DASHSCOPE_API_KEY`（中文）/ `OPENAI_API_KEY`（英文） |

---

## 七、覆盖的接口清单

类：`RealTimeASR`、`RealTimeOpenAIASR`、`RealTimeTTS`、`RealTimeOpenAITTS`、`OpenAIAPI`、`WebrtcVad`、`Vad`

函数：`play_audio`、`set_volume`、`speak_text`、`voice_save`、`normalize`、`int2float`、`downsample_block`、`get_device`、`get_max_sample_rate`、`log_print`

`large_models` 各节点实际调用点：

- `vocal_detect.py`：`RealTimeASR` / `RealTimeOpenAIASR` + `update_session` + `play_audio`
- `tts_node.py`：`RealTimeTTS` / `RealTimeOpenAITTS` + `set_volume`
- `agent_process.py`：`OpenAIAPI.llm` / `.vllm`
- `vllm_*` / `llm_*`：`play_audio`、`OpenAIAPI`
- `awake.py`：`CircleMic` / `WonderEchoPro`（串口唤醒，未改动）

---

## 八、回滚到旧 .so

前提：旧 `speech.so` 只认 **Python 3.10**，在 Ubuntu 24.04 / 3.12 上**无法直接回滚使用**。所以"回滚"分三层，按成本从低到高选。

### 8.1 先保留备份（迁移前就该做）

```bash
# 在 22.04 机器上
python3 -c "import speech,os; print(os.path.dirname(os.path.dirname(speech.__file__)))"
cp -a <上面输出的目录>/speech ~/speech_3.10_backup

# 更稳的是备份整个 SDK 目录（含 .so / resources / setup.py）
cp -a ~/large_models/speech_pkg ~/speech_sdk_backup
```

备份里应包含 `speech/speech.so` 和 `speech/resources/detect_*.so`，两者必须同版本。

### 8.2 新机器上停用兼容层（回到"没有 speech"的状态）

```bash
cd ~/ros2_ws

# 若之前用 pip 装过，先卸掉
pip3 uninstall -y speech 2>/dev/null

# 让 colcon 跳过本包
touch src/speech/COLCON_IGNORE
colcon build
source install/setup.bash

# 验证：应报 ModuleNotFoundError，说明兼容层已彻底停用
python3 -c "import speech"
```

恢复兼容层只需删掉标记再编译：

```bash
rm src/speech/COLCON_IGNORE
colcon build --packages-select speech
```

### 8.3 真正回到旧 .so（要么整机回 22.04，要么 Docker）

**整机回退**：刷回 Ubuntu 22.04 + Python 3.10，用备份重新安装：

```bash
cd ~/speech_sdk_backup
pip3 install .
python3 -c "from speech import speech; print(speech.__file__)"   # 应指向 speech.so
```

**容器方案**（留在 24.04，但把机器人跑到 22.04 环境）：

```bash
docker run -it --network host \
  --device /dev/ttyCH341USB0 \
  -v ~/ros2_ws:/root/ros2_ws \
  ubuntu:22.04
```

容器内还需装 ROS2 Humble、Python 3.10、音频依赖，并映射 `/dev/snd`；成本比兼容层高，仅作兜底。

### 8.4 只回退某个节点（不推荐）

把该节点的 `from speech import speech` 改成硬编码加载旧 `.so` 的路径。只适合临时排障，容易和其他节点版本不一致，问题排查完后必须撤销。

### 8.5 回滚后的验证清单

```bash
python3 -c "import sys; print(sys.version)"                      # 必须是 3.10.x
python3 -c "from speech import speech as s; print(s.__file__)"   # 必须是 .so
python3 -c "from speech import awake; print(awake.__file__)"     # .py 即可
```

三项都对，才说明真的回到了旧实现。
