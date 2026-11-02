package www.walle.ai.net;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.webrtc.AudioSource;
import org.webrtc.AudioTrack;
import org.webrtc.DataChannel;
import org.webrtc.IceCandidate;
import org.webrtc.MediaConstraints;
import org.webrtc.MediaStream;
import org.webrtc.PeerConnection;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.RtpReceiver;
import org.webrtc.SdpObserver;
import org.webrtc.SessionDescription;

import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * WHIP（WebRTC-HTTP Ingestion Protocol）音频上行会话：手机麦克风 → Opus → WebRTC → SRS/Oryx。
 *
 * <p>为什么用 WHIP 而不是 RTMP：RTMP 走 TCP，没有丢帧/追帧机制，网络抖动与各级缓冲会累积成
 * 秒级延迟（实测 10s）；WebRTC 走 UDP + 抖动缓冲（约 100ms）+ 丢晚到包，端到端实测约 150ms，
 * 与视频方向（WHEP）同级。</p>
 *
 * <p>流程：</p>
 * <ol>
 *   <li>用共享的 {@link PeerConnectionFactory} 建一条只发音频的 PeerConnection；</li>
 *   <li>createOffer → setLocalDescription → 等 ICE 收集完成（SRS 不支持 trickle ICE）；</li>
 *   <li>POST SDP 到 {@code /rtc/v1/whip/?app=live&stream={room}_audio&secret=...}；</li>
 *   <li>setRemoteDescription(answer)，在 PeerConnection 状态变成 CONNECTED 时回调 {@link Callback#onConnected()}。</li>
 * </ol>
 */
public final class WhipAudioSession {

    private static final String TAG = "WhipAudio";
    private static final String STREAM_ID = "phone-audio";

    public interface Callback {
        /** 连接已建立（DTLS 握手完成、可以发音频），此时才通知设备来拉流。 */
        void onConnected();

        /** 协商或连接失败；已在主线程。 */
        void onError(String message);
    }

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();
    private final PeerConnectionFactory factory;

    private PeerConnection peerConnection;
    private AudioSource audioSource;
    private AudioTrack audioTrack;
    private Callback callback;
    private String sessionLocation;
    private volatile boolean running;
    private boolean connected;

    public WhipAudioSession(PeerConnectionFactory factory) {
        this.factory = factory;
    }

    public boolean isRunning() {
        return running;
    }

    /** 开始推流；whipUrl 是已替换好房间号的完整地址（含 secret）。 */
    public void start(final String whipUrl, Callback cb) {
        if (running || factory == null) {
            return;
        }
        running = true;
        connected = false;
        callback = cb;

        PeerConnection pc = createPeerConnection();
        if (pc == null) {
            fail("PeerConnection 创建失败");
            return;
        }
        peerConnection = pc;

        try {
            audioSource = factory.createAudioSource(new MediaConstraints());
            audioTrack = factory.createAudioTrack("phone-mic", audioSource);
            audioTrack.setEnabled(true);
            pc.addTrack(audioTrack, Collections.singletonList(STREAM_ID));
        } catch (Exception e) {
            fail("创建音频轨道失败: " + e.getMessage());
            return;
        }

        pc.createOffer(new SimpleSdpObserver() {
            @Override
            public void onCreateSuccess(SessionDescription offer) {
                PeerConnection connection = peerConnection;
                if (connection == null || !running) {
                    return;
                }
                connection.setLocalDescription(new SimpleSdpObserver() {
                    @Override
                    public void onSetSuccess() {
                        waitForIceGatheringThenSend(whipUrl);
                    }

                    @Override
                    public void onSetFailure(String err) {
                        fail("设置本地 SDP 失败: " + err);
                    }
                }, offer);
            }

            @Override
            public void onCreateFailure(String err) {
                fail("创建 Offer 失败: " + err);
            }
        }, new MediaConstraints());
    }

    /** 结束推流并释放本会话资源；共享 Factory 不动（由视频会话负责释放）。 */
    public void stop() {
        running = false;
        final String location = sessionLocation;
        sessionLocation = null;
        disconnect();

        if (location != null && !location.isEmpty()) {
            networkExecutor.execute(() -> WhepClient.deleteSession(location));
        }
        // 等 DELETE 发出后再关闭线程池（给 100ms 余量，避免还没发出去就被 shutdown）
        mainHandler.postDelayed(() -> networkExecutor.shutdown(), 300L);
    }

    // ---------------------------------------------------------------- 内部

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
        return factory.createPeerConnection(config, observer);
    }

    private void waitForIceGatheringThenSend(final String whipUrl) {
        mainHandler.postDelayed(new Runnable() {
            private int tries;

            @Override
            public void run() {
                PeerConnection pc = peerConnection;
                if (pc == null || !running) {
                    return;
                }
                tries++;
                boolean complete =
                        pc.iceGatheringState() == PeerConnection.IceGatheringState.COMPLETE;
                if (complete || tries > 60) {
                    sendOffer(pc, whipUrl);
                } else {
                    mainHandler.postDelayed(this, 100);
                }
            }
        }, 100);
    }

    private void sendOffer(PeerConnection pc, final String whipUrl) {
        SessionDescription local = pc.getLocalDescription();
        final String offerSdp = local == null ? null : local.description;
        if (offerSdp == null) {
            fail("本地 SDP 为空");
            return;
        }
        networkExecutor.execute(() -> {
            try {
                WhepClient.WhepResponse response = WhepClient.postOffer(whipUrl, offerSdp);
                sessionLocation = response.location;
                mainHandler.post(() -> applyAnswer(response.sdpAnswer));
            } catch (Exception e) {
                String message = e.getMessage() == null ? "WHIP 请求失败" : e.getMessage();
                fail(message);
            }
        });
    }

    private void applyAnswer(String sdpAnswer) {
        PeerConnection pc = peerConnection;
        if (pc == null || !running) {
            return;
        }
        SessionDescription answer =
                new SessionDescription(SessionDescription.Type.ANSWER, sdpAnswer);
        pc.setRemoteDescription(new SimpleSdpObserver() {
            @Override
            public void onSetSuccess() {
                Log.i(TAG, "已应用 WHIP answer，等待媒体连接");
            }

            @Override
            public void onSetFailure(String err) {
                fail("设置远端 SDP 失败: " + err);
            }
        }, answer);
    }

    private void disconnect() {
        if (peerConnection != null) {
            try {
                peerConnection.dispose();
            } catch (Exception ignored) {
            }
            peerConnection = null;
        }
        if (audioTrack != null) {
            try {
                audioTrack.dispose();
            } catch (Exception ignored) {
            }
            audioTrack = null;
        }
        if (audioSource != null) {
            try {
                audioSource.dispose();
            } catch (Exception ignored) {
            }
            audioSource = null;
        }
        connected = false;
    }

    private void fail(String message) {
        Log.e(TAG, "音频上行失败: " + message);
        boolean wasRunning = running;
        running = false;
        disconnect();
        mainHandler.post(() -> {
            Callback cb = callback;
            if (wasRunning && cb != null) cb.onError(message);
        });
    }

    private final PeerConnection.Observer observer = new PeerConnection.Observer() {
        @Override
        public void onSignalingChange(PeerConnection.SignalingState state) {
        }

        @Override
        public void onIceConnectionChange(PeerConnection.IceConnectionState state) {
        }

        @Override
        public void onConnectionChange(PeerConnection.PeerConnectionState newState) {
            Log.i(TAG, "PeerConnection state = " + newState);
            if (newState == PeerConnection.PeerConnectionState.CONNECTED && !connected) {
                connected = true;
                mainHandler.post(() -> {
                    Callback cb = callback;
                    if (running && cb != null) cb.onConnected();
                });
            } else if (newState == PeerConnection.PeerConnectionState.FAILED) {
                // 不能在 WebRTC 回调线程里 dispose 自己，切主线程再清理
                mainHandler.post(() ->
                        fail("WebRTC 连接失败（检查 WHIP 地址 / secret / 网络）"));
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
    };

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
