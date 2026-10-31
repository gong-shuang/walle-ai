package www.walle.ai.net;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaRecorder;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import java.nio.ByteBuffer;

import io.antmedia.rtmp_client.RtmpClient;

/**
 * 手机麦克风 → AAC → FLV → RTMP 的最简实现（任务 2「反向音频」）。
 *
 * <p>链路：{@link AudioRecord} 采 44.1kHz/单声道 PCM → {@link MediaCodec} 编 AAC-LC
 * → 本类自己拼 FLV audio tag → {@link RtmpClient#write(byte[])} 交给 librtmp 发送。</p>
 *
 * <p>为什么只做音频：视频方向是设备推到 RTMP、App 用 WHEP 拉；反向只需要一条
 * 低码率音频通道。librtmp 的 write 接受的是<b>已封装好的 FLV tag 数据流</b>
 * （不是裸 AAC），所以这里必须自己拼 tag：11 字节 tag 头 + 2 字节 AAC 头 + 载荷
 * + 4 字节 previousTagSize，具体见 {@link #buildTag}。</p>
 *
 * <p>时间戳单调递增、从 0 开始；首帧前先发 AAC sequence header（AudioSpecificConfig），
 * 否则设备端 ffmpeg 无法解析编码参数。</p>
 */
public class RtmpAudioStreamer {

    private static final String TAG = "RtmpAudio";

    private static final int SAMPLE_RATE = 44100;
    private static final int CHANNELS = 1;
    private static final int BITRATE = 64000;
    /** 每次读 PCM 的字节数：约 23ms（44100 × 2 字节 × 0.023）。 */
    private static final int PCM_READ_BYTES = 2048;

    private static final String MIME_AAC = "audio/mp4a-latm";

    public interface Callback {
        /** RTMP 连接（握手 + publish）成功，此时才可以通知设备来拉流。 */
        void onConnected();

        /** 连接或编码失败；已在主线程。 */
        void onError(String message);
    }

    private final Object lock = new Object();
    private final Handler main = new Handler(Looper.getMainLooper());

    private volatile boolean running;
    private Thread worker;
    private Callback callback;

    public boolean isRunning() {
        return running;
    }

    /**
     * 开始推流（幂等：重复调用会被忽略）。
     *
     * @param rtmpUrl 完整推流地址，例如 {@code rtmp://host/live/{room}_audio?secret=...}
     */
    public void start(String rtmpUrl, Callback cb) {
        synchronized (lock) {
            if (running) return;
            running = true;
            callback = cb;
            worker = new Thread(() -> run(rtmpUrl), "rtmp-audio");
            worker.start();
        }
    }

    /** 停止采集并断开 RTMP；等待工作线程收尾（最多 400ms，避免阻塞 UI）。 */
    public void stop() {
        Thread t;
        synchronized (lock) {
            running = false;
            t = worker;
            worker = null;
        }
        if (t != null && t != Thread.currentThread()) {
            try {
                t.join(400);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    // ---------------------------------------------------------------- 工作线程

    private void run(String url) {
        RtmpClient rtmp = new RtmpClient();
        AudioRecord recorder = null;
        MediaCodec codec = null;
        try {
            rtmp.setSendTimeout(5000);
            rtmp.open(url, true);           // true = publish 模式
            postConnected();

            MediaFormat format = MediaFormat.createAudioFormat(MIME_AAC, SAMPLE_RATE, CHANNELS);
            format.setInteger(MediaFormat.KEY_BIT_RATE, BITRATE);
            format.setInteger(MediaFormat.KEY_AAC_PROFILE,
                    MediaCodecInfo.CodecProfileLevel.AACObjectLC);
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, PCM_READ_BYTES);
            codec = MediaCodec.createEncoderByType(MIME_AAC);
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);

            int minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            int bufferSize = Math.max(minBuffer, PCM_READ_BYTES * 4);
            recorder = new AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize);
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("AudioRecord 初始化失败");
            }

            codec.start();
            recorder.startRecording();

            Muxer muxer = new Muxer(rtmp);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            byte[] pcm = new byte[PCM_READ_BYTES];
            long startedMs = SystemClock.elapsedRealtime();

            while (running) {
                int read = recorder.read(pcm, 0, pcm.length);
                if (read > 0) {
                    int input = codec.dequeueInputBuffer(2000);
                    if (input >= 0) {
                        ByteBuffer in = codec.getInputBuffer(input);
                        if (in != null) {
                            in.clear();
                            in.put(pcm, 0, read);
                            long ptsUs = (SystemClock.elapsedRealtime() - startedMs) * 1000L;
                            codec.queueInputBuffer(input, 0, read, ptsUs, 0);
                        }
                    }
                }
                drain(codec, info, muxer);
            }
        } catch (Exception e) {
            Log.w(TAG, "音频推流中断: " + e);
            postError(e);
        } finally {
            running = false;
            try {
                if (recorder != null) recorder.stop();
            } catch (Exception ignore) {
            }
            try {
                if (recorder != null) recorder.release();
            } catch (Exception ignore) {
            }
            try {
                if (codec != null) {
                    codec.stop();
                    codec.release();
                }
            } catch (Exception ignore) {
            }
            try {
                rtmp.close();
            } catch (Exception ignore) {
            }
        }
    }

    /** 把编码器已产出的帧全部取空并写进 RTMP。 */
    private void drain(MediaCodec codec, MediaCodec.BufferInfo info, Muxer muxer)
            throws Exception {
        while (true) {
            int out = codec.dequeueOutputBuffer(info, 0);
            if (out == MediaCodec.INFO_TRY_AGAIN_LATER) return;
            if (out == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                ByteBuffer csd = codec.getOutputFormat().getByteBuffer("csd-0");
                if (csd != null && !muxer.sentSequence()) {
                    byte[] config = new byte[csd.remaining()];
                    csd.get(config);
                    muxer.send(config, true, 0);
                }
                continue;
            }
            if (out < 0) continue;

            ByteBuffer buffer = codec.getOutputBuffer(out);
            if (buffer != null && info.size > 0) {
                byte[] aac = new byte[info.size];
                buffer.position(info.offset);
                buffer.limit(info.offset + info.size);
                buffer.get(aac);
                boolean isConfig = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                if (isConfig) {
                    if (!muxer.sentSequence()) muxer.send(aac, true, 0);
                } else {
                    muxer.send(aac, false, info.presentationTimeUs / 1000L);
                }
            }
            codec.releaseOutputBuffer(out, false);
        }
    }

    // ---------------------------------------------------------------- 回调

    private void postConnected() {
        main.post(() -> {
            Callback cb = callback;
            if (running && cb != null) cb.onConnected();
        });
    }

    private void postError(Exception e) {
        String msg = e.getClass().getSimpleName()
                + (e.getMessage() == null ? "" : ": " + e.getMessage());
        main.post(() -> {
            Callback cb = callback;
            if (cb != null) cb.onError(msg);
        });
    }

    // ---------------------------------------------------------------- FLV 封装

    /** 只负责「AAC 帧 → FLV audio tag → RTMP」，保证时间戳单调。 */
    private static final class Muxer {
        private final RtmpClient rtmp;
        private boolean sequence;
        private long lastTsMs;

        Muxer(RtmpClient rtmp) {
            this.rtmp = rtmp;
        }

        boolean sentSequence() {
            return sequence;
        }

        void send(byte[] aac, boolean seq, long tsMs) throws RtmpClient.RtmpIOException {
            if (aac == null || aac.length == 0) return;
            if (tsMs < lastTsMs) tsMs = lastTsMs;
            lastTsMs = tsMs;
            if (seq) sequence = true;
            int ts = (int) Math.min(tsMs, 0xFFFFFFL);
            byte[] tag = buildTag(aac, seq, ts);
            int written = rtmp.write(tag);
            if (written <= 0) {
                throw new IllegalStateException("RTMP 写入失败（返回 " + written + "）");
            }
        }

        /**
         * FLV audio tag（13 字节固定头 + 载荷 + 4 字节 previousTagSize）：
         * <pre>
         * 0x08 | size(3) | ts(3) | tsExt(1) | streamId(3) |
         * 0xAE (AAC/44kHz/16bit/mono) | 0x00 序列头 / 0x01 原始帧 | AAC 数据 |
         * previousTagSize(4)
         * </pre>
         */
        private static byte[] buildTag(byte[] aac, boolean seq, int tsMs) {
            int dataSize = aac.length + 2;
            byte[] b = new byte[11 + dataSize + 4];
            b[0] = 0x08;                                              // audio
            b[1] = (byte) ((dataSize >> 16) & 0xFF);
            b[2] = (byte) ((dataSize >> 8) & 0xFF);
            b[3] = (byte) (dataSize & 0xFF);
            b[4] = (byte) ((tsMs >> 16) & 0xFF);
            b[5] = (byte) ((tsMs >> 8) & 0xFF);
            b[6] = (byte) (tsMs & 0xFF);
            b[7] = (byte) ((tsMs >> 24) & 0xFF);
            b[8] = 0;                                                 // streamId 低 3 字节
            b[9] = 0;
            b[10] = 0;
            b[11] = (byte) 0xAE;                                      // AAC, 44kHz, 16bit, mono
            b[12] = (byte) (seq ? 0x00 : 0x01);
            System.arraycopy(aac, 0, b, 13, aac.length);
            int prev = 11 + dataSize;
            int p = 13 + aac.length;
            b[p] = (byte) ((prev >> 24) & 0xFF);
            b[p + 1] = (byte) ((prev >> 16) & 0xFF);
            b[p + 2] = (byte) ((prev >> 8) & 0xFF);
            b[p + 3] = (byte) (prev & 0xFF);
            return b;
        }
    }
}
