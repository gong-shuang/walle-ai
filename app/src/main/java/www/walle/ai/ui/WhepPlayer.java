package www.walle.ai.ui;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.webrtc.AudioTrack;
import org.webrtc.DataChannel;
import org.webrtc.DefaultVideoDecoderFactory;
import org.webrtc.DefaultVideoEncoderFactory;
import org.webrtc.EglBase;
import org.webrtc.IceCandidate;
import org.webrtc.MediaConstraints;
import org.webrtc.MediaStream;
import org.webrtc.MediaStreamTrack;
import org.webrtc.PeerConnection;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.RendererCommon;
import org.webrtc.RtpReceiver;
import org.webrtc.RtpTransceiver;
import org.webrtc.SessionDescription;
import org.webrtc.SurfaceViewRenderer;
import org.webrtc.VideoTrack;
import org.webrtc.audio.JavaAudioDeviceModule;

import java.util.Collections;
import java.util.Map;
import java.util.WeakHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;

import www.walle.ai.net.WhepClient;

/**
 * 页面内嵌的 WHEP（WebRTC）拉流控制器。
 *
 * <p>与全屏的 {@link PlayerActivity} 复用同一套协商流程，但把渲染控件交给调用方，
 * 因此可以嵌在控制页的视频区域里播放。一个实例对应一次拉流会话：
 * 开始 → {@link #start(String)}；停止/离开页面 → {@link #release()}。
 *
 * <p><b>渲染器只能 init 一次</b>：{@link SurfaceViewRenderer#init} 在已初始化时会抛
 * {@code IllegalStateException: videoRendererAlready initialized}。因此这里：
 * <ul>
 *   <li>同一 View 上复用同一个 {@link RendererSink}（用 WeakHashMap 绑定），只 init 一次；</li>
 *   <li>首帧回调通过 sink 转发给「当前」播放器，避免复用时回调仍指向已释放的实例；</li>
 *   <li>释放时解除绑定并 release 渲染器，下一次播放可以安全地重新 init。</li>
 * </ul>
 */
public final class WhepPlayer {

    private static final String TAG = "WhepPlayer";

    public interface Callback {
        /** 状态文案变化（主线程） */
        void onStatus(String text);

        /** 首帧上屏（主线程） */
        void onFirstFrame();

        /** 播放失败（主线程） */
        void onError(String message);
    }

    /**
     * 每个 {@link SurfaceViewRenderer} 只允许 init 一次；这里保存它的 RendererEvents，
     * 并把首帧事件转发给当前持有它的 {@link WhepPlayer}。
     */
    private static final class RendererSink implements RendererCommon.RendererEvents {
        volatile WhepPlayer owner;

        @Override
        public void onFirstFrameRendered() {
            WhepPlayer p = owner;
            if (p != null) p.notifyFirstFrame();
        }

        @Override
        public void onFrameResolutionChanged(int videoWidth, int videoHeight, int rotation) {
        }
    }

    /** 渲染器 → 已注册的 sink（弱引用键，View 销毁后可被回收）。 */
    private static final Map<SurfaceViewRenderer, RendererSink> SINKS = new WeakHashMap<>();

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();
    private final AtomicReference<String> sessionLocation = new AtomicReference<>();
    private final Callback callback;
    private final Context appContext;

    private EglBase eglBase;
    private SurfaceViewRenderer renderer;
    private PeerConnectionFactory factory;
    private JavaAudioDeviceModule audioDeviceModule;
    private PeerConnection peerConnection;
    private boolean released;
    private boolean isLive;

    public WhepPlayer(Context context, SurfaceViewRenderer renderer, Callback callback) {
        this.appContext = context.getApplicationContext();
        this.renderer = renderer;
        this.callback = callback;
        initWebRtc();
    }

    /** 开始拉流：whepUrl 形如 https://host/rtc/v1/whep/?app=live&stream={room} 已替换好的完整地址。 */
    public void start(String whepUrl) {
        if (released) {
            return;
        }
        createAndNegotiate(whepUrl);
    }

    // ---------------- WebRTC 初始化 --------------

    private void initWebRtc() {
        eglBase = EglBase.create();
        if (renderer != null) {
            RendererSink sink;
            synchronized (SINKS) {
                sink = SINKS.get(renderer);
                if (sink == null) {
                    sink = new RendererSink();
                    // 只有第一次需要 init；重复 init 会抛 videoRendererAlready initialized
                    renderer.init(eglBase.getEglBaseContext(), sink);
                    SINKS.put(renderer, sink);
                    Log.i(TAG, "渲染器首次初始化");
                } else {
                    Log.w(TAG, "渲染器已初始化，复用（跳过 init）");
                }
            }
            sink.owner = this;
            // 等比例缩放完整显示，不做裁剪
            renderer.setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT);
        }

        PeerConnectionFactory.initialize(
                PeerConnectionFactory.InitializationOptions
                        .builder(appContext)
                        .createInitializationOptions()
        );

        audioDeviceModule = JavaAudioDeviceModule.builder(appContext).createAudioDeviceModule();

        factory = PeerConnectionFactory.builder()
                .setAudioDeviceModule(audioDeviceModule)
                .setVideoEncoderFactory(
                        new DefaultVideoEncoderFactory(eglBase.getEglBaseContext(), true, true))
                .setVideoDecoderFactory(
                        new DefaultVideoDecoderFactory(eglBase.getEglBaseContext()))
                .createPeerConnectionFactory();
    }

    private PeerConnection createPeerConnection() {
        if (factory == null) {
            return null;
        }
        PeerConnection.RTCConfiguration config =
                new PeerConnection.RTCConfiguration(Collections.<PeerConnection.IceServer>emptyList());
        config.sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN;
        config.continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE;
        config.bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE;
        config.rtcpMuxPolicy = PeerConnection.RtcpMuxPolicy.REQUIRE;
        config.tcpCandidatePolicy = PeerConnection.TcpCandidatePolicy.ENABLED;
        config.keyType = PeerConnection.KeyType.ECDSA;
        return factory.createPeerConnection(config, peerConnectionObserver);
    }

    // ---------------- 协商流程 --------------

    private void createAndNegotiate(final String whepUrl) {
        PeerConnection pc = createPeerConnection();
        if (pc == null) {
            error("PeerConnection 创建失败");
            return;
        }
        peerConnection = pc;

        try {
            RtpTransceiver.RtpTransceiverInit audioInit =
                    new RtpTransceiver.RtpTransceiverInit(
                            RtpTransceiver.RtpTransceiverDirection.RECV_ONLY);
            RtpTransceiver.RtpTransceiverInit videoInit =
                    new RtpTransceiver.RtpTransceiverInit(
                            RtpTransceiver.RtpTransceiverDirection.RECV_ONLY);
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO, audioInit);
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO, videoInit);
        } catch (Exception e) {
            error("添加收发通道失败: " + e.getMessage());
            return;
        }

        status("正在创建连接…");
        pc.createOffer(new SimpleSdpObserver() {
            @Override
            public void onCreateSuccess(SessionDescription offer) {
                PeerConnection connection = peerConnection;
                if (connection == null) {
                    return;
                }
                connection.setLocalDescription(new SimpleSdpObserver() {
                    @Override
                    public void onSetSuccess() {
                        waitForIceGatheringThenSend(whepUrl);
                    }

                    @Override
                    public void onSetFailure(String err) {
                        error("设置本地 SDP 失败: " + err);
                    }
                }, offer);
            }

            @Override
            public void onCreateFailure(String err) {
                error("创建 Offer 失败: " + err);
            }
        }, new MediaConstraints());
    }

    /** SRS/Oryx 的 WHEP 不支持 trickle ICE，需等 candidate 收集完成后一次性 POST 完整 SDP。 */
    private void waitForIceGatheringThenSend(final String whepUrl) {
        mainHandler.postDelayed(new Runnable() {
            private int tries;

            @Override
            public void run() {
                PeerConnection pc = peerConnection;
                if (pc == null || released) {
                    return;
                }
                tries++;
                boolean complete =
                        pc.iceGatheringState() == PeerConnection.IceGatheringState.COMPLETE;
                if (complete || tries > 60) {
                    sendOffer(pc, whepUrl);
                } else {
                    mainHandler.postDelayed(this, 100);
                }
            }
        }, 100);
    }

    private void sendOffer(PeerConnection pc, final String whepUrl) {
        SessionDescription localDescription = pc.getLocalDescription();
        final String offerSdp = localDescription == null ? null : localDescription.description;
        if (offerSdp == null) {
            error("本地 SDP 为空");
            return;
        }
        status("正在与服务器协商…");
        networkExecutor.execute(() -> {
            try {
                WhepClient.WhepResponse response = WhepClient.postOffer(whepUrl, offerSdp);
                sessionLocation.set(response.location);
                mainHandler.post(() -> applyAnswer(response.sdpAnswer));
            } catch (Exception e) {
                String message = e.getMessage() == null ? "WHEP 请求失败" : e.getMessage();
                mainHandler.post(() -> error(message));
            }
        });
    }

    private void applyAnswer(String sdpAnswer) {
        PeerConnection pc = peerConnection;
        if (pc == null || released) {
            return;
        }
        SessionDescription answer =
                new SessionDescription(SessionDescription.Type.ANSWER, sdpAnswer);
        pc.setRemoteDescription(new SimpleSdpObserver() {
            @Override
            public void onSetSuccess() {
                status("已建立信令，等待媒体流…");
            }

            @Override
            public void onSetFailure(String err) {
                error("设置远端 SDP 失败: " + err);
            }
        }, answer);
    }

    // ---------------- WebRTC 事件 --------------

    private final PeerConnection.Observer peerConnectionObserver = new PeerConnection.Observer() {
        @Override
        public void onSignalingChange(PeerConnection.SignalingState state) {
        }

        @Override
        public void onIceConnectionChange(PeerConnection.IceConnectionState state) {
        }

        @Override
        public void onConnectionChange(PeerConnection.PeerConnectionState newState) {
            Log.i(TAG, "PeerConnection state = " + newState);
            if (newState == PeerConnection.PeerConnectionState.CONNECTED) {
                isLive = true;
                status("● 推流中");
            } else if (newState == PeerConnection.PeerConnectionState.DISCONNECTED) {
                status("连接中断，尝试恢复…");
            } else if (newState == PeerConnection.PeerConnectionState.FAILED) {
                if (!released) status("连接失败");
            } else if (newState == PeerConnection.PeerConnectionState.CLOSED) {
                if (!released) status("连接已关闭");
            }
        }

        @Override
        public void onIceConnectionReceivingChange(boolean receiving) {
        }

        @Override
        public void onIceGatheringChange(PeerConnection.IceGatheringState state) {
        }

        @Override
        public void onIceCandidate(IceCandidate candidate) {
            // 非 trickle 模式，candidate 已包含在完整 SDP 中
        }

        @Override
        public void onIceCandidatesRemoved(IceCandidate[] candidates) {
        }

        @Override
        public void onAddStream(MediaStream stream) {
        }

        @Override
        public void onRemoveStream(MediaStream stream) {
        }

        @Override
        public void onDataChannel(DataChannel dataChannel) {
        }

        @Override
        public void onRenegotiationNeeded() {
        }

        @Override
        public void onAddTrack(RtpReceiver receiver, MediaStream[] streams) {
        }

        @Override
        public void onTrack(RtpTransceiver transceiver) {
            if (transceiver == null || transceiver.getReceiver() == null) {
                return;
            }
            MediaStreamTrack track = transceiver.getReceiver().track();
            if (track == null) {
                return;
            }
            if (track instanceof VideoTrack) {
                VideoTrack videoTrack = (VideoTrack) track;
                videoTrack.setEnabled(true);
                if (renderer != null) {
                    videoTrack.addSink(renderer);
                }
            } else if (track instanceof AudioTrack) {
                AudioTrack audioTrack = (AudioTrack) track;
                audioTrack.setEnabled(true);
                try {
                    audioTrack.setVolume(1.0);
                } catch (Exception ignored) {
                }
            }
        }
    };

    // ---------------- 资源释放 --------------

    /** 幂等释放：结束 WHEP 会话 + 拆 WebRTC 资源 + 释放渲染器。 */
    public void release() {
        if (released) {
            return;
        }
        released = true;

        mainHandler.removeCallbacksAndMessages(null);

        final String location = sessionLocation.getAndSet(null);
        networkExecutor.execute(() -> WhepClient.deleteSession(location));
        networkExecutor.shutdown();

        if (peerConnection != null) {
            try {
                peerConnection.dispose();
            } catch (Exception ignored) {
            }
            peerConnection = null;
        }
        if (factory != null) {
            try {
                factory.dispose();
            } catch (Exception ignored) {
            }
            factory = null;
        }
        if (renderer != null) {
            synchronized (SINKS) {
                SINKS.remove(renderer);
            }
            try {
                renderer.release();
            } catch (Exception ignored) {
            }
            renderer = null;
        }
        if (eglBase != null) {
            try {
                eglBase.release();
            } catch (Exception ignored) {
            }
            eglBase = null;
        }
        if (audioDeviceModule != null) {
            try {
                audioDeviceModule.release();
            } catch (Exception ignored) {
            }
            audioDeviceModule = null;
        }
    }

    public boolean isLive() {
        return isLive;
    }

    /** 已释放的实例不能再 start()，调用方应新建一个。 */
    public boolean isReleased() {
        return released;
    }

    // ---------------- 回调封装 --------------

    private void status(final String text) {
        if (released) return;
        mainHandler.post(() -> {
            if (!released && callback != null) callback.onStatus(text);
        });
    }

    private void notifyFirstFrame() {
        isLive = true;
        if (callback != null) callback.onFirstFrame();
    }

    private void error(final String message) {
        if (released) return;
        Log.e(TAG, "播放错误: " + message);
        mainHandler.post(() -> {
            if (!released && callback != null) callback.onError(message);
        });
    }

    /** 简化版 SdpObserver：只关心成功/失败分支。 */
    private static class SimpleSdpObserver implements org.webrtc.SdpObserver {
        @Override
        public void onCreateSuccess(SessionDescription sessionDescription) {
        }

        @Override
        public void onSetSuccess() {
        }

        @Override
        public void onCreateFailure(String s) {
        }

        @Override
        public void onSetFailure(String s) {
        }
    }
}
