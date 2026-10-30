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
import org.webrtc.SdpObserver;
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
 * WHEP（WebRTC-HTTP Egress Protocol）拉流会话：把「渲染器 + WebRTC + WHEP 协商」封装成一个可复用组件。
 *
 * <p>控制页的内嵌小窗（{@link ControlFragment}）和全屏播放页（{@link PlayerActivity}）
 * 用的是同一份实现，页面只负责自己的 UI（状态文案、首帧遮罩、摄像头切换、退出流程）。</p>
 *
 * <p>生命周期：</p>
 * <ol>
 *   <li>构造：创建 EGL / PeerConnectionFactory / ADM，初始化渲染器（<b>同一个 View 只 init 一次</b>）；</li>
 *   <li>{@link #start(String)}：发起一次 WHEP 协商（内部会先关掉旧会话，可重复调用）；</li>
 *   <li>{@link #stopSession()}：只结束当前会话（DELETE 服务端会话 + dispose PeerConnection），
 *       保留 EGL/Factory，便于之后快速重新协商；</li>
 *   <li>{@link #release()}：完整释放，不可再用。</li>
 * </ol>
 */
public final class WhepSession {

    private static final String TAG = "WhepSession";

    /** 会话回调，全部在主线程触发。 */
    public interface Callback {
        /** 过程文案（正在创建连接 / 正在协商 / 已建立信令…）。 */
        void onStatus(String text);

        /** 首帧上屏。 */
        void onFirstFrame();

        /** 播放失败（协商失败、WHEP 请求失败等）。 */
        void onError(String message);

        /** PeerConnection 状态变化；页面可据此显示「直播中 / 连接中断」等文案。 */
        default void onConnectionState(PeerConnection.PeerConnectionState state) {
        }
    }

    /**
     * 每个 {@link SurfaceViewRenderer} 只能 init 一次；这里保存它的 RendererEvents，
     * 并把首帧事件转发给当前持有它的会话。
     */
    private static final class RendererSink implements RendererCommon.RendererEvents {
        volatile WhepSession owner;

        @Override
        public void onFirstFrameRendered() {
            WhepSession s = owner;
            if (s != null) s.notifyFirstFrame();
        }

        @Override
        public void onFrameResolutionChanged(int videoWidth, int videoHeight, int rotation) {
        }
    }

    /** 渲染器 → 已注册的 sink（弱引用键，View 销毁后可回收）。 */
    private static final Map<SurfaceViewRenderer, RendererSink> SINKS = new WeakHashMap<>();

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();
    private final Context appContext;
    private final Callback callback;

    private SurfaceViewRenderer renderer;
    private EglBase eglBase;
    private PeerConnectionFactory factory;
    private JavaAudioDeviceModule audioDeviceModule;
    private PeerConnection peerConnection;
    private final AtomicReference<String> sessionLocation = new AtomicReference<>();

    private volatile boolean released;
    private volatile boolean isLive;

    public WhepSession(Context context, SurfaceViewRenderer renderer, Callback callback) {
        this.appContext = context.getApplicationContext();
        this.renderer = renderer;
        this.callback = callback;
        initWebRtc();
    }

    /** 开始（或重新开始）一次拉流；whepUrl 是已替换好房间号的完整 WHEP 地址。 */
    public void start(String whepUrl) {
        if (released) {
            return;
        }
        stopSession();
        negotiate(whepUrl);
    }

    public boolean isLive() {
        return isLive;
    }

    /** 已释放的实例不可再用，调用方应新建一个。 */
    public boolean isReleased() {
        return released;
    }

    // ---------------------------------------------------------------- 初始化

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

    // ---------------------------------------------------------------- 协商

    private void negotiate(final String whepUrl) {
        isLive = false;
        PeerConnection pc = createPeerConnection();
        if (pc == null) {
            error("PeerConnection 创建失败");
            return;
        }
        peerConnection = pc;

        try {
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO,
                    new RtpTransceiver.RtpTransceiverInit(
                            RtpTransceiver.RtpTransceiverDirection.RECV_ONLY));
            pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO,
                    new RtpTransceiver.RtpTransceiverInit(
                            RtpTransceiver.RtpTransceiverDirection.RECV_ONLY));
        } catch (Exception e) {
            error("添加收发通道失败: " + e.getMessage());
            return;
        }

        status("正在创建连接…");
        pc.createOffer(new SimpleSdpObserver() {
            @Override
            public void onCreateSuccess(SessionDescription offer) {
                PeerConnection connection = peerConnection;
                if (connection == null || released) {
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

    /** SRS/Oryx 的 WHEP 不支持 trickle ICE，需要等 candidate 收集完成后一次性 POST 完整 SDP。 */
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

    // ---------------------------------------------------------------- WebRTC 事件

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
            }
            // 文案交给页面决定（控制页写「推流中」、播放页写「直播中」）
            // 注意：这里来自 WebRTC 的 signaling 线程，必须切主线程再回调
            dispatch(() -> callback.onConnectionState(newState));
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
                SurfaceViewRenderer r = renderer;
                if (r != null) {
                    videoTrack.addSink(r);
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

    // ---------------------------------------------------------------- 释放

    /** 只结束当前会话：DELETE 服务端会话 + dispose PeerConnection，保留 EGL/Factory。 */
    public void stopSession() {
        mainHandler.removeCallbacksAndMessages(null);
        final String location = sessionLocation.getAndSet(null);
        if (location != null) {
            networkExecutor.execute(() -> WhepClient.deleteSession(location));
        }
        if (peerConnection != null) {
            try {
                peerConnection.dispose();
            } catch (Exception ignored) {
            }
            peerConnection = null;
        }
        // 清掉上一帧，避免旧画面残留到新会话建立完成
        if (renderer != null) {
            try {
                renderer.clearImage();
            } catch (Exception ignored) {
            }
        }
        isLive = false;
    }

    /** 完整释放：结束会话 + 拆 WebRTC 资源 + 释放渲染器。不可逆。 */
    public void release() {
        if (released) {
            return;
        }
        released = true;

        stopSession();

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
        networkExecutor.shutdown();
    }

    // ---------------------------------------------------------------- 回调封装

    /**
     * 统一的回调派发：切主线程 + 兜底捕获异常。
     *
     * <p>这些回调大多由 WebRTC 的 native 线程（signaling / network）触发。异常一旦从这些线程
     * 跨过 JNI 边界返回，WebRTC 的 jni_generator_helper 会命中
     * {@code Check failed: !env->ExceptionCheck()} 直接 SIGABRT —— 表现为「UI 里一个
     * IllegalStateException，整个 App 闪退」。所以这里必须自己 try/catch，把异常留在 Java 层。</p>
     */
    private void dispatch(Runnable action) {
        if (released) return;
        mainHandler.post(() -> {
            if (released) return;
            try {
                action.run();
            } catch (Throwable t) {
                Log.e(TAG, "回调执行异常（已拦截，避免跨 JNI 崩溃）: " + t, t);
            }
        });
    }

    private void status(final String text) {
        dispatch(() -> callback.onStatus(text));
    }

    private void notifyFirstFrame() {
        dispatch(callback::onFirstFrame);
    }

    private void error(final String message) {
        Log.e(TAG, "播放错误: " + message);
        dispatch(() -> callback.onError(message));
    }

    /** 简化版 SdpObserver：只关心成功/失败分支。 */
    private static class SimpleSdpObserver implements SdpObserver {
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
