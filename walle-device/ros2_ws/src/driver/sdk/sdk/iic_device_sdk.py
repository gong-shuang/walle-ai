#!/usr/bin/env python3
# encoding: utf-8
# 幻尔 IIC 设备库：超声波模块 + 语音交互模块
# Hiwonder IIC device library: sonar + speech interaction module

import sys
import time
from smbus2 import SMBus, i2c_msg

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)


class Sonar:
    """IIC 超声波模块，默认地址 0x77"""
    __units = {"mm": 0, "cm": 1}
    __dist_reg = 0

    __RGB_MODE = 2
    __RGB1_R = 3
    __RGB1_G = 4
    __RGB1_B = 5
    __RGB2_R = 6
    __RGB2_G = 7
    __RGB2_B = 8

    __RGB1_R_BREATHING_CYCLE = 9
    __RGB1_G_BREATHING_CYCLE = 10
    __RGB1_B_BREATHING_CYCLE = 11
    __RGB2_R_BREATHING_CYCLE = 12
    __RGB2_G_BREATHING_CYCLE = 13
    __RGB2_B_BREATHING_CYCLE = 14

    def __init__(self):
        self.i2c_addr = 0x77
        self.i2c = 1
        self.Pixels = [0, 0]
        self.RGBMode = 0

    def __getattr(self, attr):
        if attr in self.__units:
            return self.__units[attr]
        if attr == "Distance":
            return self.getDistance()
        else:
            raise AttributeError('Unknow attribute : %s' % attr)

    def setRGBMode(self, mode):
        try:
            with SMBus(self.i2c) as bus:
                bus.write_byte_data(self.i2c_addr, self.__RGB_MODE, mode)
        except BaseException as e:
            print(e)

    def show(self):  # 占位，与扩展板RGB保持调用一致
        pass

    def numPixels(self):
        return 2

    def setPixelColor(self, index, rgb):
        color = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
        try:
            if index != 0 and index != 1:
                return
            start_reg = 3 if index == 0 else 6
            with SMBus(self.i2c) as bus:
                bus.write_byte_data(self.i2c_addr, start_reg, 0xFF & (color >> 16))
                bus.write_byte_data(self.i2c_addr, start_reg + 1, 0xFF & (color >> 8))
                bus.write_byte_data(self.i2c_addr, start_reg + 2, 0xFF & color)
                self.Pixels[index] = color
        except BaseException as e:
            print(e)

    def getPixelColor(self, index):
        if index != 0 and index != 1:
            raise ValueError("Invalid pixel index", index)
        return ((self.Pixels[index] >> 16) & 0xFF,
                (self.Pixels[index] >> 8) & 0xFF,
                self.Pixels[index] & 0xFF)

    def setBreathCycle(self, index, rgb, cycle):
        try:
            if index != 0 and index != 1:
                return
            if rgb < 0 or rgb > 2:
                return
            start_reg = 9 if index == 0 else 12
            cycle = int(cycle / 100)
            with SMBus(self.i2c) as bus:
                bus.write_byte_data(self.i2c_addr, start_reg + rgb, cycle)
        except BaseException as e:
            print(e)

    def startSymphony(self):
        self.setRGBMode(1)
        self.setBreathCycle(1, 0, 2000)
        self.setBreathCycle(1, 1, 3300)
        self.setBreathCycle(1, 2, 4700)
        self.setBreathCycle(2, 0, 4600)
        self.setBreathCycle(2, 1, 2000)
        self.setBreathCycle(2, 2, 3400)

    def getDistance(self):
        with SMBus(self.i2c) as bus:
            msg = i2c_msg.write(self.i2c_addr, [0, ])
            bus.i2c_rdwr(msg)
            read = i2c_msg.read(self.i2c_addr, 2)
            bus.i2c_rdwr(read)
            dist = int.from_bytes(bytes(list(read)), byteorder='little', signed=False)
        # 超量程按 5000mm 处理；I2C 异常向上抛出，由调用方决定告警与重试
        return min(dist, 5000)


class SpeechModule:
    """IIC 一体化语音交互模块，默认地址 0x34

    识别结果寄存器 0x64 返回的常用值：
      1 前进 / go
      2 后退 / back
      3 左转 / left
      4 右转 / right
      9 停止 / stop
      15 开始语音识别 / begin speech recognition
      16 结束语音识别 / end speech recognition
    """

    #类型
    CMD_COMMAND = 0x00     # 命令词条播报语
    CMD_ANNOUNCER = 0xFF   # 普通播报语

    #语音识别结果寄存器地址，通过不断读取此地址的值判断是否识别到语音，不同的值对应不同的语音
    RESULT_REG = 0x64
    #播报设置寄存器地址
    SPEAK_REG = 0x6E

    # 命令 CMD_COMMAND
    RESULT_GO = 1
    RESULT_BACK = 2
    RESULT_LEFT = 3
    RESULT_RIGHT = 4
    RESULT_STOP = 9
    RESULT_ASR_START = 15
    RESULT_ASR_STOP = 16

    #播报语 CMD_ANNOUNCER
    #前方有障碍物
    VOICE_OBSTACLE = 0x05
    #完成泊车
    VOICE_PARKING = 0x06
    
    

    def __init__(self, address=0x34, bus=1):
        self.address = address
        self.bus = bus
        self._i2c = SMBus(bus)

    def read_result(self):
        """读取一次语音识别结果，未识别到返回 0"""
        try:
            data = self._i2c.read_i2c_block_data(self.address, self.RESULT_REG, 1)
            return int(data[0]) if data else 0
        except OSError:
            return 0

    def speak(self, cmd, voice_id):
        """播报固定语句，cmd 为 CMD_COMMAND 或 CMD_ANNOUNCER"""
        if cmd not in (self.CMD_COMMAND, self.CMD_ANNOUNCER):
            return False
        try:
            self._i2c.write_i2c_block_data(self.address, self.SPEAK_REG, [cmd, int(voice_id)])
            return True
        except OSError:
            return False

    def close(self):
        try:
            self._i2c.close()
        except Exception:
            pass

    def speak_obstacle(self):
        """播报前方是否有障碍物"""
        return self.speak(self.CMD_ANNOUNCER, self.VOICE_OBSTACLE)

    def speak_parking(self):
        """播报完成泊车"""
        return self.speak(self.CMD_ANNOUNCER, self.VOICE_PARKING)


if __name__ == '__main__':
    sonar = Sonar()
    speech = SpeechModule()
    while True:
        print('distance: %s mm, speech result: %s' % (sonar.getDistance(), speech.read_result()))
        time.sleep(0.2)
