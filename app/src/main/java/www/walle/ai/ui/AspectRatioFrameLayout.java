package www.walle.ai.ui;

import android.content.Context;
import android.util.AttributeSet;
import android.view.View;
import android.widget.FrameLayout;

/**
 * 固定宽高比（默认 4:3，即 640x480）的 FrameLayout。
 *
 * <p>用于视频预览区域：在第一次测量时就把高度算成 宽度 × 3/4，
 * 避免 wrap_content 下 SurfaceViewRenderer 先被测量成可用最大高度、
 * 下一次布局再被压缩，从而出现“先全屏、再变成 4:3”的跳动。</p>
 */
public class AspectRatioFrameLayout extends FrameLayout {

    /** 高度 / 宽度，默认 0.75 即 4:3。 */
    private float heightRatio = 480f / 640f;

    public AspectRatioFrameLayout(Context context) {
        super(context);
    }

    public AspectRatioFrameLayout(Context context, AttributeSet attrs) {
        super(context, attrs);
    }

    public AspectRatioFrameLayout(Context context, AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);
    }

    public void setHeightRatio(float heightRatio) {
        if (heightRatio > 0f && this.heightRatio != heightRatio) {
            this.heightRatio = heightRatio;
            requestLayout();
        }
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int widthMode = MeasureSpec.getMode(widthMeasureSpec);
        int widthSize = MeasureSpec.getSize(widthMeasureSpec);
        if (widthMode == MeasureSpec.UNSPECIFIED || widthSize <= 0) {
            super.onMeasure(widthMeasureSpec, heightMeasureSpec);
            return;
        }
        int height = Math.round(widthSize * heightRatio);
        super.onMeasure(
                MeasureSpec.makeMeasureSpec(widthSize, MeasureSpec.EXACTLY),
                MeasureSpec.makeMeasureSpec(height, MeasureSpec.EXACTLY));
    }
}
