package www.walle.ai.ui;

import android.content.res.Resources;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageDecoder;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.util.LruCache;
import android.widget.ImageView;

import androidx.annotation.DrawableRes;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import www.walle.ai.R;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 引导页整屏插图的按需解码加载器。
 *
 * <p>背景：三张 1200×2667 的 WebP 若用 {@link ImageView#setImageResource(int)} 直接加载，
 * 每张会按原尺寸解成 ARGB_8888 位图（约 12.2 MB），ViewPager2 预加载相邻页时三张同时存活，
 * 峰值接近 37 MB，低端机上很容易 OOM。</p>
 *
 * <p>这里做的三件事：</p>
 * <ol>
 *   <li><b>按控件尺寸解码</b>：API 28+ 用 {@link ImageDecoder} 指定目标尺寸（cover 适配，
 *       不放大），低版本用 {@link BitmapFactory} 的 2 的幂采样，避免解出远超屏幕的位图；</li>
 *   <li><b>优先使用 GPU 位图</b>：API 26+ 首选 {@link Bitmap.Config#HARDWARE}，
 *       像素放在显存而不是 Java 堆，显著降低低端机的堆压力；不支持时自动回退 RGB_565（省一半内存）；</li>
 *   <li><b>后台解码 + 小容量缓存</b>：解码在单线程执行器里做，主线程只负责 setImageBitmap；
 *       最多缓存 3 张，划回上一页不重复解码。View 被回收时用 tag 校验丢弃过期结果。</li>
 * </ol>
 */
final class OnboardingImageLoader {

    private static final String TAG = "OnboardingImage";

    /** 缓存 3 张：当前页 + 左右相邻页，与 ViewPager2 的预加载窗口一致。 */
    private static final int MAX_CACHED = 3;

    private final Resources res;
    private final int targetW;
    private final int targetH;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());

    /** 值为 0 的占位：表示「正在解码」，避免同一张图被重复提交。 */
    private final LruCache<Integer, Bitmap> cache = new LruCache<Integer, Bitmap>(MAX_CACHED) {
        @Override
        protected int sizeOf(@NonNull Integer key, @NonNull Bitmap value) {
            return 1;
        }
        // 逐出时不手动 recycle：位图可能仍被某个已回收但未释放的 View 引用，
        // 直接 recycle 会抛「trying to use a recycled bitmap」。API 26+ 位图位于
        // native 堆，交给 GC 释放更安全。
    };

    OnboardingImageLoader(@NonNull Resources res) {
        this.res = res;
        // 整屏插图铺满全屏（centerCrop），解码目标就是屏幕尺寸
        targetW = res.getDisplayMetrics().widthPixels;
        targetH = res.getDisplayMetrics().heightPixels;
    }

    /**
     * 异步把图片加载到 ImageView。
     * <p>会先把 view 的 tag 设成本次资源 id，解码回到主线程时再校验一次，
     * 防止 ViewPager2 复用 View 后把上一张图刷到新的页面上。</p>
     */
    void load(@NonNull final ImageView view, @DrawableRes final int resId) {
        view.setTag(R.id.ivPage, resId);
        Bitmap cached = cache.get(resId);
        if (cached != null && !cached.isRecycled()) {
            view.setImageBitmap(cached);
            return;
        }
        view.setImageDrawable(null);
        executor.execute(() -> {
            final Bitmap bmp;
            try {
                bmp = decode(resId);
            } catch (Throwable t) {
                Log.w(TAG, "decode failed resId=" + resId, t);
                return;
            }
            main.post(() -> {
                if (bmp == null) return;
                if (!Integer.valueOf(resId).equals(view.getTag(R.id.ivPage))) {
                    // View 已经被复用到别的页面，这张图不再需要（交给 GC）
                    return;
                }
                view.setImageBitmap(bmp);
                cache.put(resId, bmp);
            });
        });
    }

    /** 页面销毁时调用，停止后台线程。缓存里的位图交给 GC / LruCache 逐出。 */
    void shutdown() {
        executor.shutdownNow();
        main.removeCallbacksAndMessages(null);
    }

    // ---------------------------------------------------------------- 解码

    @Nullable
    private Bitmap decode(@DrawableRes int resId) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            Bitmap hardware = decodeWithImageDecoder(resId, ImageDecoder.ALLOCATOR_HARDWARE);
            if (hardware != null) return hardware;
            // 个别设备/驱动不支持 hardware 位图，回退软件解码
            Bitmap software = decodeWithImageDecoder(resId, ImageDecoder.ALLOCATOR_SOFTWARE);
            if (software != null) return software;
        }
        return decodeWithBitmapFactory(resId);
    }

    /**
     * API 28+：按目标尺寸解码，避免解出整张原图。
     * <p>目标尺寸按 centerCrop 语义计算（scale = max(target/src)，且不超过 1，避免放大失真），
     * 因此屏幕上不会出现空白边，也不会浪费像素。</p>
     */
    @Nullable
    private Bitmap decodeWithImageDecoder(@DrawableRes int resId, int allocator) {
        try {
            ImageDecoder.Source source = ImageDecoder.createSource(res, resId);
            return ImageDecoder.decodeBitmap(source, (decoder, info, s) -> {
                int srcW = info.getSize().getWidth();
                int srcH = info.getSize().getHeight();
                if (srcW > 0 && srcH > 0) {
                    float scale = Math.max((float) targetW / srcW, (float) targetH / srcH);
                    scale = Math.min(scale, 1f);
                    int w = Math.max(1, Math.round(srcW * scale));
                    int h = Math.max(1, Math.round(srcH * scale));
                    if (w != srcW || h != srcH) decoder.setTargetSize(w, h);
                }
                decoder.setMemorySizePolicy(ImageDecoder.MEMORY_POLICY_LOW_RAM);
                decoder.setAllocator(allocator);
            });
        } catch (Throwable t) {
            Log.w(TAG, "ImageDecoder failed (allocator=" + allocator + ")", t);
            return null;
        }
    }

    /**
     * API 26–27：用 2 的幂采样解码，并尽量用 RGB_565（每像素 2 字节，比 ARGB_8888 省一半）。
     * <p>先只读尺寸头（inJustDecodeBounds）算采样率，再真正解码，不会把原图完整解进内存。</p>
     */
    @Nullable
    private Bitmap decodeWithBitmapFactory(@DrawableRes int resId) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeResource(res, resId, bounds);

        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight);
        opts.inPreferredConfig = Bitmap.Config.RGB_565;
        opts.inScaled = false;
        // 关掉密度缩放，避免系统再按 nodpi/屏幕密度做一次隐式放大
        opts.inDensity = 0;
        opts.inTargetDensity = 0;
        try {
            Bitmap bmp = BitmapFactory.decodeResource(res, resId, opts);
            if (bmp != null) return bmp;
        } catch (Throwable t) {
            Log.w(TAG, "BitmapFactory decode failed", t);
        }
        // 兜底：不指定配置再解一次
        BitmapFactory.Options fallback = new BitmapFactory.Options();
        fallback.inSampleSize = opts.inSampleSize;
        try {
            return BitmapFactory.decodeResource(res, resId, fallback);
        } catch (Throwable t) {
            Log.w(TAG, "BitmapFactory fallback failed", t);
            return null;
        }
    }

    /** 采样率取 2 的幂，保证解出的图仍然 ≥ 目标尺寸（只缩不放）。 */
    private int sampleSize(int srcW, int srcH) {
        if (srcW <= 0 || srcH <= 0) return 1;
        int sample = 1;
        while (srcW / (sample * 2) >= targetW && srcH / (sample * 2) >= targetH) {
            sample *= 2;
        }
        return sample;
    }
}
