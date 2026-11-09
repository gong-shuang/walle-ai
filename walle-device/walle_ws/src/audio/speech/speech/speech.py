#!/usr/bin/env python3
# encoding: utf-8
"""speech 包的纯 Python 兼容实现（替代幻尔 speech.so，支持 Python 3.12+）。

覆盖 TurboPi 的 large_models 节点实际使用的接口：
    RealTimeASR, RealTimeOpenAIASR, RealTimeTTS, RealTimeOpenAITTS,
    OpenAIAPI, play_audio, set_volume, speak_text, WebrtcVad
以及内部依赖的工具函数（voice_save / normalize / int2float / downsample_block /
get_device / get_max_sample_rate / log_print）。

第三方依赖全部延迟导入，保证 import speech 本身不会因缺少某个 SDK 而失败。
"""
import base64
import os
import re
import subprocess
import tempfile
import threading
import time
import wave

ASR_RATE = 16000
_CHUNK_MS = 100
_CHUNK_BYTES = int(ASR_RATE * _CHUNK_MS / 1000) * 2  # 16bit -> 3200 bytes

_np = None


def _numpy():
    global _np
    if _np is None:
        import numpy as np
        _np = np
    return _np


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def protected_print(text, *args, **kwargs):
    """打印但不因管道关闭而抛 BrokenPipeError。"""
    try:
        print(text, *args, **kwargs)
    except BrokenPipeError:
        pass


def log_print(text, log):
    """有 ROS logger 就走 logger，没有就打印。"""
    if log is not None:
        try:
            log.info(text)
            return
        except Exception:
            pass
    protected_print(text)


def play_audio(file_path, block=True):
    """用 aplay 播放 wav 文件。"""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    proc = subprocess.Popen(['aplay', '-q', file_path],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    if block:
        proc.wait()
        return proc.returncode == 0
    return proc


def set_volume(volume):
    """设置播放音量（0-100），自动找有音量控件的声卡，失败返回 False。"""
    volume = max(0, min(100, int(volume)))
    for card in range(0, 5):
        for ctl in ('PCM', 'Master', 'PCM Playback Volume', 'Speaker'):
            try:
                proc = subprocess.run(['amixer', '-c', str(card), 'sset', ctl,
                                       '%d%%' % volume],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
                if proc.returncode == 0:
                    return True
            except OSError:
                continue
    return False


def get_device(cmd):
    """执行 cmd，解析第一个 `card N: ... device M`，返回 (card, device)。"""
    out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
    m = re.search(r'card (\d+):[^\n]*?device (\d+)', out)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def get_max_sample_rate(device):
    """查询录音设备支持的最大采样率。"""
    try:
        proc = subprocess.run(
            ['arecord', '-D', 'plughw:%s,0' % device, '--dump-hw-params', '/dev/null'],
            capture_output=True, text=True, timeout=5)
        m = re.search(r'RATE:\s*([^\n]+)', proc.stdout + proc.stderr)
        if m:
            rates = [int(x) for x in re.findall(r'\d+', m.group(1))]
            if rates:
                return max(rates)
    except Exception:
        pass
    try:
        import sounddevice as sd
        info = sd.query_devices(device)
        return int(info['default_samplerate'])
    except Exception:
        return None


def voice_save(voice_path, raw_data, channel, audio_format, samplerate):
    """把裸 PCM(int16) 存成 wav。"""
    with wave.open(voice_path, 'wb') as wf:
        wf.setnchannels(int(channel))
        wf.setsampwidth(2)
        wf.setframerate(int(samplerate))
        wf.writeframes(raw_data)
    return voice_path


def normalize(snd_data):
    """与厂商实现一致：按峰值缩放到 1/4 量程。"""
    np = _numpy()
    data = np.asarray(snd_data, dtype=np.int16)
    peak = int(np.max(np.abs(data.astype(np.float32))) if data.size else 0)
    if peak == 0:
        return data
    return (data.astype(np.float32) * np.float32(8192.0 / peak)).astype(np.int16)


def int2float(sound):
    np = _numpy()
    return np.asarray(sound, dtype=np.float32) / 32768.0


def downsample_block(data, orig_sr=48000, target_sr=16000):
    """整数倍/有理数倍降采样。"""
    np = _numpy()
    arr = np.asarray(data)
    if orig_sr == target_sr:
        return arr
    from scipy.signal import resample_poly
    y = resample_poly(arr.astype(np.float32), int(target_sr), int(orig_sr), axis=0)
    if np.issubdtype(arr.dtype, np.integer):
        y = np.clip(y, -32768, 32767)
        return y.astype(np.int16)
    return y


# ---------------------------------------------------------------------------
# 录音
# ---------------------------------------------------------------------------
def _read_wav_frames(path):
    with wave.open(path, 'rb') as wf:
        return wf.readframes(wf.getnframes())


def _record_wav(path, rate=ASR_RATE, channel=None, device=None,
                begin_silence=3.0, end_silence=1.5, max_seconds=10.0, log=None):
    """录音到 wav（16k 单声道）。未检测到人声返回 False。"""
    np = _numpy()
    import sounddevice as sd
    import webrtcvad

    try:
        info = sd.query_devices(device, 'input')
    except Exception as e:
        log_print('no input device %s: %s' % (device, e), log)
        return False

    native = int(info.get('default_samplerate') or rate)
    max_ch = int(info.get('max_input_channels') or 1)
    ch = int(channel) if channel else 1
    ch = max(1, min(ch, max_ch))

    block = int(native * 0.03)  # 30ms
    vad = webrtcvad.Vad(0)
    frames = []
    speech_started = False
    start = last_voice = time.time()

    try:
        with sd.InputStream(device=device, channels=ch, samplerate=native,
                            dtype='int16', blocksize=block) as stream:
            while True:
                data, _ = stream.read(block)
                mono = downsample_block(data[:, 0], native, ASR_RATE) if native != ASR_RATE \
                    else data[:, 0]
                mono = np.asarray(mono, dtype=np.int16)
                # 30ms 一帧喂 VAD
                for i in range(0, len(mono) - 479, 480):
                    chunk = mono[i:i + 480]
                    voiced = vad.is_speech(chunk.tobytes(), ASR_RATE)
                    if voiced:
                        speech_started = True
                        last_voice = time.time()
                    if speech_started:
                        frames.append(chunk)
                now = time.time()
                if not speech_started and now - start > begin_silence:
                    log_print('no voice detected', log)
                    return False
                if speech_started and now - last_voice > end_silence:
                    break
                if now - start > max_seconds:
                    break
    except Exception as e:
        log_print('record failed: %s' % e, log)
        return False

    if not frames:
        return False
    pcm = np.concatenate(frames).astype(np.int16)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(ASR_RATE)
        wf.writeframes(pcm.tobytes())
    return True


def _temp_wav():
    fd, path = tempfile.mkstemp(suffix='.wav')
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# VAD 录音器（对齐原 WebrtcVad 接口）
# ---------------------------------------------------------------------------
class WebrtcVad:
    def __init__(self, model=0, silence_seconds_begin=3, record_seconds=8,
                 silence_ratio=0.8, channel=2, device=None):
        self.model = model
        self.silence_seconds_begin = silence_seconds_begin
        self.record_seconds = record_seconds
        self.silence_ratio = silence_ratio
        self.channel = channel
        self.device = device
        self._interrupted = False

    def interrupt(self):
        self._interrupted = True

    def vad_thread(self, log):
        return None

    def voice_recording(self, save_path, log=None):
        return _record_wav(save_path,
                           channel=self.channel,
                           device=self.device,
                           begin_silence=self.silence_seconds_begin,
                           end_silence=1.5,
                           max_seconds=self.record_seconds,
                           log=log)


class Vad:
    """Silero VAD 的替代实现：统一走 webrtcvad，避免在 Pi 上加载 torch。"""

    def __init__(self, voiced_confidence=0.1, silence_seconds_begin=3, record_seconds=8,
                 silence_ratio=0.8, channel=None, device=None):
        self._vad = WebrtcVad(model=0,
                              silence_seconds_begin=silence_seconds_begin,
                              record_seconds=record_seconds,
                              silence_ratio=silence_ratio,
                              channel=channel or 2,
                              device=device)

    def interrupt(self):
        self._vad.interrupt()

    def vad_thread(self, log):
        return None

    def voice_recording(self, save_path, log=None):
        return self._vad.voice_recording(save_path, log)


# ---------------------------------------------------------------------------
# 中文实时 ASR（阿里云 paraformer）
# ---------------------------------------------------------------------------
class RealTimeASR:
    def __init__(self, rate=16000, channel=None, device=None, log=None):
        self.log = log
        self.rate = int(rate)
        self.channel = channel
        self.device = device

    def _ensure_key(self):
        try:
            import dashscope
        except ImportError as e:
            raise RuntimeError('dashscope not installed: pip3 install dashscope') from e
        if not dashscope.api_key:
            dashscope.api_key = os.environ.get('DASHSCOPE_API_KEY', '')
        if not dashscope.api_key:
            raise RuntimeError('dashscope api key is empty '
                               '(set dashscope.api_key or DASHSCOPE_API_KEY)')
        return dashscope

    def asr(self, model='paraformer-realtime-v2', save_path=None,
            semantic_punctuation_enabled=False, max_sentence_silence=1500):
        dashscope = self._ensure_key()
        from dashscope.audio.asr import Recognition, RecognitionCallback

        path = save_path or _temp_wav()
        if not _record_wav(path, rate=self.rate, channel=self.channel,
                           device=self.device, max_seconds=10.0, log=self.log):
            return ''

        texts = []
        done = threading.Event()

        class _Callback(RecognitionCallback):
            def on_event(self, result):
                try:
                    sentence = result.get_sentence()
                except Exception:
                    return
                if sentence and sentence.get('sentence_end'):
                    texts.append(sentence.get('text', ''))
                elif sentence and sentence.get('text') and not texts:
                    texts[:] = [sentence['text']]

            def on_complete(self):
                done.set()

            def on_error(self, result):
                log_print('asr error: %s' % result, self.log)
                done.set()

            def on_close(self):
                done.set()

        recognition = Recognition(model=model, format='pcm', sample_rate=self.rate,
                                  callback=_Callback(),
                                  semantic_punctuation_enabled=semantic_punctuation_enabled,
                                  max_sentence_silence=max_sentence_silence)
        try:
            recognition.start()
            pcm = _read_wav_frames(path)
            for i in range(0, len(pcm), _CHUNK_BYTES):
                recognition.send_audio_frame(pcm[i:i + _CHUNK_BYTES])
        finally:
            try:
                recognition.stop()
            except Exception:
                pass

        done.wait(timeout=5.0)
        result = ''.join(texts).strip()
        log_print('asr result: %s' % result, self.log)
        return result


# ---------------------------------------------------------------------------
# 英文 ASR（OpenAI transcription）
# ---------------------------------------------------------------------------
class RealTimeOpenAIASR:
    def __init__(self, rate=16000, channel=None, device=None, log=None):
        self.log = log
        self.rate = int(rate)
        self.channel = channel
        self.device = device
        self.model = 'gpt-4o-transcribe'
        self.language = 'en'
        self.prompt = ''
        self._session = {
            'threshold': 0.2,
            'prefix_padding_ms': 300,
            'silence_duration_ms': 800,
        }

    def update_session(self, model='gpt-4o-transcribe', language='en', prompt='',
                       threshold=0.2, prefix_padding_ms=300, silence_duration_ms=800):
        """原实现用于 realtime 会话参数；这里保存下来供转录接口使用。"""
        self.model = model or self.model
        self.language = language or self.language
        self.prompt = prompt
        self._session.update(threshold=threshold,
                             prefix_padding_ms=prefix_padding_ms,
                             silence_duration_ms=silence_duration_ms)
        return True

    config_session = update_session

    # 以下方法仅为保持与原库一致的对象接口；当前实现走 HTTP 转录，不用实时 websocket。
    def on_open(self, ws):
        return None

    def on_message(self, ws, message):
        return None

    def on_error(self, ws, error):
        log_print('websocket error: %s' % error, self.log)

    def on_close(self, ws, close_status_code, close_msg):
        return None

    def websocket_thread(self):
        return None

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=os.environ.get('OPENAI_API_KEY'),
                      base_url=os.environ.get('OPENAI_BASE_URL') or None,
                      timeout=60)

    def asr(self, save_path=None):
        path = save_path or _temp_wav()
        if not _record_wav(path, rate=self.rate, channel=self.channel,
                           device=self.device, max_seconds=10.0, log=self.log):
            return ''
        try:
            with open(path, 'rb') as f:
                resp = self._client().audio.transcriptions.create(
                    model=self.model or 'whisper-1',
                    file=f,
                    language=(self.language or None),
                    prompt=self.prompt or None)
            text = getattr(resp, 'text', '') or ''
            text = text.strip()
            log_print('asr result: %s' % text, self.log)
            return text
        except Exception as e:
            log_print('openai asr failed: %s' % e, self.log)
            return ''


# ---------------------------------------------------------------------------
# 中文 TTS（阿里云 sambert / cosyvoice）
# ---------------------------------------------------------------------------
class RealTimeTTS:
    def __init__(self, samplerate=16000, channels=1, device=None, log=None):
        self.samplerate = int(samplerate)
        self.channels = channels
        self.device = device
        self.log = log

    def _ensure_key(self):
        try:
            import dashscope
        except ImportError as e:
            raise RuntimeError('dashscope not installed: pip3 install dashscope') from e
        if not dashscope.api_key:
            dashscope.api_key = os.environ.get('DASHSCOPE_API_KEY', '')
        if not dashscope.api_key:
            raise RuntimeError('dashscope api key is empty '
                               '(set dashscope.api_key or DASHSCOPE_API_KEY)')
        return dashscope

    def _synthesize(self, text, model, voice):
        dashscope = self._ensure_key()
        if model.startswith('cosyvoice'):
            from dashscope.audio.tts_v2 import SpeechSynthesizer
            synth = SpeechSynthesizer(model=model, voice=voice or 'longxiaochun')
            return synth.call(text)
        from dashscope.audio.tts import SpeechSynthesizer
        # sambert 系列的 voice 由 model 决定，传空即可
        # dashscope 2.x 中 AudioFormat 为 SpeechSynthesizer 的内部类，
        # call() 的 format 参数可直接传字符串，这里不再引用 AudioFormat
        result = SpeechSynthesizer.call(model=model, text=text,
                                        format='wav',
                                        sample_rate=self.samplerate)
        return result.get_audio_data()

    def tts(self, text='', model='sambert-zhiming-v1', voice='', save_path='',
            play=True, block=False):
        if not text:
            return ''
        audio = self._synthesize(text, model, voice)
        if not audio:
            raise RuntimeError('tts returned empty audio')
        path = save_path or _temp_wav()
        with open(path, 'wb') as f:
            f.write(audio)
        if play:
            play_audio(path, block=block)
        return path


# ---------------------------------------------------------------------------
# 英文 TTS（OpenAI audio speech）
# ---------------------------------------------------------------------------
class RealTimeOpenAITTS:
    def __init__(self, log=None):
        self.log = log

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=os.environ.get('OPENAI_API_KEY'),
                      base_url=os.environ.get('OPENAI_BASE_URL') or None,
                      timeout=60)

    def save_audio(self, text: 'str', model='tts-1', voice='onyx', speed=1.0,
                   instructions='', audio_format='wav', save_path='speech.wav'):
        if not text:
            return save_path
        kwargs = dict(model=model, voice=voice, input=text,
                      response_format=audio_format)
        if model and 'gpt-4o' in model:
            if speed != 1.0:
                kwargs['speed'] = float(speed)
            if instructions:
                kwargs['instructions'] = instructions
        with self._client().audio.speech.with_streaming_response.create(**kwargs) as resp:
            resp.stream_to_file(save_path)
        return save_path

    def stream_and_play(self, text: 'str', model='tts-1', voice='onyx', speed=1.0,
                        instructions=''):
        path = _temp_wav()
        self.save_audio(text, model=model, voice=voice, speed=speed,
                        instructions=instructions, save_path=path)
        play_audio(path, block=True)

    def stream_audio_playback(self, audio_queue: 'queue.Queue'):
        """播放队列中的 wav 路径，空字符串表示结束。"""
        while True:
            path = audio_queue.get()
            if not path:
                break
            try:
                play_audio(path, block=True)
            except Exception as e:
                log_print('play failed: %s' % e, self.log)
            finally:
                try:
                    audio_queue.task_done()
                except Exception:
                    pass

    def tts(self, text: 'str', model='tts-1', voice='onyx', speed=1.0,
            instructions=''):
        return self.stream_and_play(text, model=model, voice=voice,
                                    speed=speed, instructions=instructions)


# ---------------------------------------------------------------------------
# 通用 OpenAI 兼容接口（LLM / VLLM / ASR）
# ---------------------------------------------------------------------------
class OpenAIAPI:
    def __init__(self, api_key, base_url):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    # ---- 图像编码 --------------------------------------------------------
    def data_process(self, data, img_w, img_h):
        """把 numpy/文件/bytes/base64 图像转成 data URL。"""
        np = _numpy()
        import cv2

        if isinstance(data, str):
            if data.startswith('data:image'):
                return data
            img = cv2.imread(data)
            if img is None:
                raise ValueError('image not found: %s' % data)
        elif isinstance(data, (bytes, bytearray)):
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        else:
            img = np.asarray(data)
            if img.dtype != np.uint8:
                img = img.astype(np.uint8)
        if img_w and img_h:
            img = cv2.resize(img, (int(img_w), int(img_h)))
        ok, buf = cv2.imencode('.jpeg', img)
        if not ok:
            raise RuntimeError('imencode failed')
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.tobytes()).decode()

    # ---- LLM -------------------------------------------------------------
    def llm(self, question, prompt='', model='qwen-max'):
        messages = []
        if prompt:
            messages.append({'role': 'system', 'content': prompt})
        messages.append({'role': 'user', 'content': question})
        resp = self.client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content

    def llm_multi_turn(self, messages, model='qwen-max'):
        resp = self.client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content

    def llm_origin(self, params):
        """原样透传参数，返回完整 response 对象。"""
        return self.client.chat.completions.create(**params)

    # ---- VLLM ------------------------------------------------------------
    def vllm(self, question, image, prompt='', model='qwen-vl-max-latest',
             detail='high'):
        url = self.data_process(image, 0, 0)
        messages = []
        if prompt:
            messages.append({'role': 'system', 'content': prompt})
        messages.append({'role': 'user', 'content': [
            {'type': 'text', 'text': question},
            {'type': 'image_url', 'image_url': {'url': url, 'detail': detail}},
        ]})
        resp = self.client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content

    def vllm_multi_turn(self, messages, image, model='qwen-vl-max-latest'):
        url = self.data_process(image, 0, 0)
        msgs = list(messages)
        item = {'type': 'image_url', 'image_url': {'url': url}}
        if msgs and msgs[-1].get('role') == 'user':
            content = msgs[-1].get('content')
            if isinstance(content, str):
                content = [{'type': 'text', 'text': content}]
            msgs[-1] = {'role': 'user', 'content': list(content) + [item]}
        else:
            msgs.append({'role': 'user', 'content': [item]})
        resp = self.client.chat.completions.create(model=model, messages=msgs)
        return resp.choices[0].message.content

    def vllm_origin(self, params):
        return self.client.chat.completions.create(**params)

    # ---- ASR -------------------------------------------------------------
    def asr(self, audio_file, model='whisper-1'):
        with open(audio_file, 'rb') as f:
            resp = self.client.audio.transcriptions.create(model=model, file=f)
        return getattr(resp, 'text', '') or ''


# ---------------------------------------------------------------------------
# 便捷接口
# ---------------------------------------------------------------------------
def speak_text(text):
    """直接播报一句话（原库没有该函数，但仓库里有调用点）。"""
    if os.environ.get('ASR_LANGUAGE', 'Chinese') == 'Chinese':
        return RealTimeTTS().tts(text=text,
                                 model=os.environ.get('TTS_MODEL', 'sambert-zhiming-v1'),
                                 block=True)
    RealTimeOpenAITTS().tts(text)
    return None
