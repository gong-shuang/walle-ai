# MCU 固件通信超时停车需求（P0-3.3）

> 适用对象：`ros_robot_controller` 控制器板 STM32 固件
> 背景：上位机进程崩溃、断网或断电后，P0-3.1 / P0-3.2 的软件看门狗全部失效，
> 必须由 MCU 自行检测通信中断并停车，形成软硬双保险。
> 状态：待固件方实现；本仓库不含固件源码。

---

## 1. 串口协议（与上位机 SDK 一致）

帧格式：

```
0xAA 0x55 FUNC LEN DATA CRC8
```

- `FUNC = 0x03`：`PACKET_FUNC_MOTOR`
- 电机帧子命令：
  - `DATA[0] = 0x01`：速度控制（`Board.set_motor_speed`）
  - `DATA[0] = 0x05`：占空比控制（`Board.set_motor_duty`）
- `DATA[1]`：电机数量；随后每个电机为 `<B f>`（id 0–3、float 值）
- `CRC8` 覆盖 `FUNC` 到 `DATA` 的全部字节

上位机实现参考：
`ros2_ws/src/car_control/ros_robot_controller/ros_robot_controller/ros_robot_controller_sdk.py`

---

## 2. 功能需求

| 编号 | 需求 |
| --- | --- |
| R1 | 上电 / 复位后四轮输出为 0，直到收到第一条合法电机帧 |
| R2 | 固件记录最后一次**合法电机帧**的时间。合法 = 帧头、长度、CRC 均正确，且 `FUNC=0x03`、子命令为 `0x01` 或 `0x05` |
| R3 | 超过 `MCU_CMD_TIMEOUT` 未收到合法电机帧时，四轮 PWM / 占空比立即置 0，并持续保持 0 |
| R4 | 超时计时只由电机帧刷新；舵机、OLED、RGB、查询等其它帧不得刷新计时 |
| R5 | 超时后收到新的合法电机帧，恢复正常控制 |
| R6 | 超时停车不得阻塞串口接收、按键、IMU 等其它功能 |
| R7 | 建议提供状态位或日志，便于上位机读取“是否发生过通信超时”（可选） |

`MCU_CMD_TIMEOUT` 建议默认 **500ms**，允许 300–500ms 配置。
上位机 `cmd_vel` 与控制节点均以 10Hz 持续下发，500ms 有足够余量。

---

## 3. 实现要点（伪代码）

```c
static uint32_t last_motor_ms;

void on_frame(uint8_t func, const uint8_t *data, size_t len, bool crc_ok) {
    if (crc_ok && func == 0x03 && len >= 2 &&
        (data[0] == 0x01 || data[0] == 0x05)) {
        last_motor_ms = now_ms();
    }
}

void safety_loop(void) {              /* 建议 10ms 周期 */
    if (now_ms() - last_motor_ms > MCU_CMD_TIMEOUT) {
        set_all_motor_pwm(0);         /* 四轮持续输出 0 */
    }
}
```

注意：`last_motor_ms` 上电初始化为 0 时，第一轮循环会立即触发停车，符合 R1。

---

## 4. 验证方法

前置条件：**车轮必须架空**，确认轮子悬空后再执行。

| # | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| 1 | 正常链路 | 上位机以 10–20Hz 连续发速度 | 持续转动，不触发超时 |
| 2 | 停发指令 | `python3 tools/test_mcu_timeout.py --speed 20 --pulse 0.3` | 停止发送后 0.5s 内停转 |
| 3 | 杀进程 | 运行 bringup 使轮子转动，`kill -9` 上位机进程 | 0.5s 内停转 |
| 4 | 整机断电 | 直接断开树莓派电源 | 0.5s 内停转 |
| 5 | 干扰帧 | 只发舵机 / OLED / RGB，不发电机帧 | 保持停止，不误启动 |
| 6 | 恢复 | 再次发送合法电机帧 | 底盘恢复响应 |

---

## 5. 验收标准

- 场景 2、3、4 全部通过，且场景 1、5 不误触发。
- 超时时间可配置，默认 500ms。
- 超时后四轮输出为 0，不依赖上位机任何进程存活。
