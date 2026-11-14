package www.walle.ai.ui;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.util.AttributeSet;
import android.view.MotionEvent;
import android.view.View;

import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;

import www.walle.ai.R;

/**
 * 虚拟摇杆：外圈固定，内圈可向任意方向拖动，松手后弹回中心。
 *
 * <p>只负责交互与归一化，不关心业务语义：回调给出的 {@code nx/ny} 是
 * 「相对最大行程的比例」，范围 [-1, 1]（x 右正、y 下正），由使用方
 * 自行判定四向/斜向与发指令的节流。</p>
 */
public class JoystickView extends View {

    public interface Listener {
        /**
         * 内圈位置变化（手指拖动中）。
         *
         * @param nx 水平偏移：-1 最左，1 最右
         * @param ny 垂直偏移：-1 最上，1 最下
         */
        void onJoystickMove(float nx, float ny);

        /** 手指松开（含取消），内圈开始回中。 */
        void onJoystickRelease();
    }

    /** 回中动画时长。 */
    private static final long RETURN_DURATION_MS = 120L;
    /** 内圈半径占外圈半径的比例（对齐设计稿约 0.44）。 */
    private static final float KNOB_RADIUS_RATIO = 0.44f;
    /** 外圈边缘留白，避免圆被裁切。 */
    private static final float EDGE_INSET_DP = 2f;

    private final Paint basePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint knobPaint = new Paint(Paint.ANTI_ALIAS_FLAG);

    /** 当前归一化偏移，回中动画会平滑改回 0。 */
    private float nx;
    private float ny;

    private int activePointerId = -1;
    private boolean dragging;

    private ValueAnimator returnAnimator;

    private final float edgeInset;

    @Nullable
    private Listener listener;

    public JoystickView(Context context) {
        this(context, null);
    }

    public JoystickView(Context context, @Nullable AttributeSet attrs) {
        this(context, attrs, 0);
    }

    public JoystickView(Context context, @Nullable AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);
        float density = getResources().getDisplayMetrics().density;
        basePaint.setColor(ContextCompat.getColor(context, R.color.joystick_base));
        knobPaint.setColor(ContextCompat.getColor(context, R.color.joystick_knob));
        edgeInset = EDGE_INSET_DP * density;
        setClickable(true);
    }

    public void setListener(@Nullable Listener listener) {
        this.listener = listener;
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        float baseRadius = baseRadius();
        float knobRadius = baseRadius * KNOB_RADIUS_RATIO;
        float travel = baseRadius - knobRadius;

        canvas.drawCircle(cx, cy, baseRadius, basePaint);
        canvas.drawCircle(cx + nx * travel, cy + ny * travel, knobRadius, knobPaint);
    }

    private float baseRadius() {
        return Math.max(0f, Math.min(getWidth(), getHeight()) / 2f - edgeInset);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN: {
                float cx = getWidth() / 2f;
                float cy = getHeight() / 2f;
                if (Math.hypot(event.getX() - cx, event.getY() - cy) > baseRadius()) {
                    return false;      // 落在圆外，不接管这次触摸
                }
                cancelReturnAnimation();
                activePointerId = event.getPointerId(0);
                dragging = true;
                applyTouch(event.getX(), event.getY());
                return true;
            }
            case MotionEvent.ACTION_MOVE: {
                if (!dragging) return false;
                int index = event.findPointerIndex(activePointerId);
                if (index < 0) return false;
                applyTouch(event.getX(index), event.getY(index));
                return true;
            }
            case MotionEvent.ACTION_POINTER_UP: {
                if (!dragging) return false;
                int index = event.getActionIndex();
                if (event.getPointerId(index) != activePointerId) return true;
                release();
                return true;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL: {
                if (!dragging) return false;
                release();
                return true;
            }
            default:
                return super.onTouchEvent(event);
        }
    }

    /** 把触点换算成归一化偏移：超出最大行程时沿半径方向钳位。 */
    private void applyTouch(float x, float y) {
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        float baseRadius = baseRadius();
        float travel = baseRadius * (1f - KNOB_RADIUS_RATIO);
        float dx = x - cx;
        float dy = y - cy;
        float distance = (float) Math.hypot(dx, dy);
        if (distance > travel && distance > 0f) {
            float scale = travel / distance;
            dx *= scale;
            dy *= scale;
        }
        nx = travel <= 0f ? 0f : dx / travel;
        ny = travel <= 0f ? 0f : dy / travel;
        invalidate();
        if (listener != null) listener.onJoystickMove(nx, ny);
    }

    private void release() {
        dragging = false;
        activePointerId = -1;
        if (listener != null) listener.onJoystickRelease();
        animateBackToCenter();
    }

    private void animateBackToCenter() {
        cancelReturnAnimation();
        final float startX = nx;
        final float startY = ny;
        if (Math.abs(startX) < 0.001f && Math.abs(startY) < 0.001f) {
            nx = 0f;
            ny = 0f;
            invalidate();
            return;
        }
        returnAnimator = ValueAnimator.ofFloat(0f, 1f);
        returnAnimator.setDuration(RETURN_DURATION_MS);
        returnAnimator.addUpdateListener(animation -> {
            float t = (float) animation.getAnimatedValue();
            nx = startX * (1f - t);
            ny = startY * (1f - t);
            invalidate();
        });
        returnAnimator.start();
    }

    private void cancelReturnAnimation() {
        if (returnAnimator != null) {
            returnAnimator.cancel();
            returnAnimator = null;
        }
    }

    @Override
    protected void onDetachedFromWindow() {
        cancelReturnAnimation();
        super.onDetachedFromWindow();
    }
}
