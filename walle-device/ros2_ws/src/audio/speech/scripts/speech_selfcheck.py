#!/usr/bin/env python3
# encoding: utf-8
"""speech 兼容层上机自检。

用法:
    python3 speech_selfcheck.py                 # 只做静态检查
    python3 speech_selfcheck.py --mic-test 3    # 额外录 3 秒并回放
    python3 speech_selfcheck.py --mic-test 3 --device 1
    ros2 run speech speech_selfcheck --mic-test 3

退出码 0 = 全部通过（警告不影响），1 = 有 FAIL。
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import wave

FAIL = []
WARN = []


def ok(msg):
    print('  [ OK ] %s' % msg)


def warn(msg):
    print('  [WARN] %s' % msg)
    WARN.append(msg)


def fail(msg):
    print('  [FAIL] %s' % msg)
    FAIL.append(msg)


def check_env():
    print('== 1. 运行环境')
    print('  arch      : %s' % platform.machine())
    print('  python    : %s' % sys.version.split()[0])
    print('  executable: %s' % sys.executable)
    print('  ASR_LANGUAGE: %s' % os.environ.get('ASR_LANGUAGE', '<未设置>'))
    if not platform.machine().startswith(('aarch64', 'arm64')):
        warn('当前架构不是 aarch64，树莓派镜像的音频设备名可能不同')
    if sys.version_info < (3, 8):
        fail('Python 版本过低: %s' % sys.version)
    else:
        ok('Python 版本可用')
    if not os.environ.get('ASR_LANGUAGE'):
        fail('ASR_LANGUAGE 未设置（config.py 会 KeyError，需 export ASR_LANGUAGE=Chinese|English）')
    else:
        ok('ASR_LANGUAGE=%s' % os.environ['ASR_LANGUAGE'])


def check_package():
    print('== 2. speech 包')
    try:
        import speech
        from speech import speech as mod
        from speech import awake
    except Exception as e:
        fail('import speech 失败: %s' % e)
        return None
    print('  package: %s' % speech.__file__)
    print('  module : %s' % mod.__file__)
    if getattr(mod, '__file__', '').endswith('.so'):
        fail('加载的仍是 .so（旧的 3.10 编译包没清理干净）')
    elif not getattr(mod, '__file__', '').endswith('.py'):
        warn('模块来源不是 .py: %s' % getattr(mod, '__file__', ''))
    else:
        ok('加载的是纯 Python 实现')

    required = ['RealTimeASR', 'RealTimeOpenAIASR', 'RealTimeTTS', 'RealTimeOpenAITTS',
                'OpenAIAPI', 'WebrtcVad', 'Vad', 'play_audio', 'set_volume',
                'speak_text', 'voice_save', 'normalize', 'int2float', 'downsample_block']
    missing = [n for n in required if not hasattr(mod, n)]
    if missing:
        fail('缺少接口: %s' % ', '.join(missing))
    else:
        ok('%d 个接口齐全' % len(required))

    if not hasattr(awake, 'CircleMic') or not hasattr(awake, 'WonderEchoPro'):
        fail('awake.py 缺少 CircleMic / WonderEchoPro')
    else:
        ok('awake.CircleMic / WonderEchoPro 可用')
    return mod


def check_deps():
    print('== 3. 第三方依赖')
    deps = [
        ('numpy', 'sudo apt install python3-numpy'),
        ('sounddevice', 'sudo apt install libportaudio2 && pip3 install sounddevice'),
        ('webrtcvad', 'pip3 install webrtcvad（需要 gcc/build-essential）'),
        ('serial', 'sudo apt install python3-serial'),
        ('scipy', 'sudo apt install python3-scipy'),
        ('openai', 'pip3 install openai'),
        ('dashscope', 'pip3 install dashscope'),
        ('cv2', 'sudo apt install python3-opencv'),
    ]
    for name, hint in deps:
        try:
            __import__(name)
            ok(name)
        except Exception as e:
            fail('%s 导入失败 (%s) -> %s' % (name, e, hint))


def check_audio(mod=None):
    print('== 4. 音频设备')
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        inputs = [(i, d) for i, d in enumerate(devs) if d['max_input_channels'] > 0]
        if not inputs:
            fail('没有可用的录音设备')
        else:
            print('  可用录音设备:')
            for i, d in inputs:
                mark = ' <- device=%d' % i
                print('    %2d  %-28s ch=%d  sr=%d%s'
                      % (i, d['name'][:28], d['max_input_channels'],
                         int(d['default_samplerate']), mark))
            ok('共 %d 个录音设备' % len(inputs))
        try:
            print('  默认输出: %s' % sd.query_devices(kind='output')['name'])
        except Exception as e:
            warn('查询默认输出失败: %s' % e)
    except Exception as e:
        fail('sounddevice 不可用: %s' % e)

    for cmd in (['aplay', '-l'], ['arecord', '-l']):
        exe = cmd[0]
        if not shutil.which(exe):
            fail('缺少命令 %s (sudo apt install alsa-utils)' % exe)
            continue
        out = subprocess.run(cmd, capture_output=True, text=True)
        lines = [l.strip() for l in out.stdout.splitlines() if 'card ' in l]
        if lines:
            ok('%s 检测到 %d 个 card' % (exe, len(lines)))
        else:
            warn('%s 没列出 card（板子上可能只有 USB 声卡）' % exe)
        for l in lines:
            print('    %s: %s' % (exe, l))

    if mod is not None and shutil.which('amixer'):
        try:
            if mod.set_volume(50):
                ok('set_volume(50) 生效')
            else:
                warn('没有可调的音量控件，set_volume() 返回 False（不影响识别/合成）')
        except Exception as e:
            warn('set_volume 测试失败: %s' % e)


def check_keys():
    print('== 5. 云端密钥')
    lang = os.environ.get('ASR_LANGUAGE', '')
    try:
        import dashscope
        key = dashscope.api_key or os.environ.get('DASHSCOPE_API_KEY', '')
        if key:
            ok('dashscope.api_key 已设置')
        elif lang == 'Chinese':
            fail('中文模式需要 dashscope.api_key（在 config.py 里赋值或导出 DASHSCOPE_API_KEY）')
        else:
            warn('dashscope.api_key 未设置（英文模式用不到）')
    except Exception:
        pass
    if os.environ.get('OPENAI_API_KEY'):
        ok('OPENAI_API_KEY 已设置')
    elif lang != 'Chinese':
        fail('英文模式需要 OPENAI_API_KEY（config.py 会写入环境变量）')
    else:
        warn('OPENAI_API_KEY 未设置（中文模式用不到）')


def mic_test(mod, seconds, device):
    print('== 6. 录音/回放测试')
    try:
        import numpy as np
        import sounddevice as sd
        info = sd.query_devices(device, 'input')
        rate = int(info['default_samplerate'])
        ch = max(1, min(2, int(info['max_input_channels'])))
        print('  录音: device=%s rate=%d ch=%d %s' % (device, rate, ch, info['name']))
        print('  说话中 ... (%ds)' % seconds)
        data = sd.rec(int(seconds * rate), samplerate=rate, channels=ch,
                      dtype='int16', device=device)
        sd.wait()
        path = tempfile.mktemp(suffix='.wav')
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(ch)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(np.asarray(data, dtype=np.int16).tobytes())
        peak = int(np.max(np.abs(data))) if data.size else 0
        if peak < 50:
            fail('录音几乎静音（peak=%d），检查麦克风接线/设备号' % peak)
        else:
            ok('录到声音 peak=%d' % peak)
        print('  回放 ...')
        mod.play_audio(path, block=True)
        ok('回放命令已执行（应能听到刚才的录音）')
        print('  wav 保留在 %s' % path)
    except Exception as e:
        fail('录音/回放失败: %s' % e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mic-test', type=float, default=0,
                        help='录音测试时长（秒），0 表示跳过')
    parser.add_argument('--device', default=1, help='录音设备号，默认 1')
    args = parser.parse_args()

    check_env()
    mod = check_package()
    check_deps()
    check_audio(mod)
    check_keys()
    if args.mic_test > 0:
        if mod is None:
            fail('包不可用，跳过录音测试')
        else:
            try:
                dev = int(args.device)
            except ValueError:
                dev = args.device
            mic_test(mod, args.mic_test, dev)

    print('=' * 46)
    print('FAIL=%d  WARN=%d' % (len(FAIL), len(WARN)))
    for m in FAIL:
        print('  FAIL:', m)
    for m in WARN:
        print('  WARN:', m)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
