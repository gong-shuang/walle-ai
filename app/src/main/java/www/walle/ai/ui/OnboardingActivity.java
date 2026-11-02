package www.walle.ai.ui;

import android.animation.ValueAnimator;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.os.Build;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.DecelerateInterpolator;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.activity.OnBackPressedCallback;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import androidx.recyclerview.widget.RecyclerView;
import androidx.viewpager2.widget.ViewPager2;

import java.util.ArrayList;
import java.util.List;

import www.walle.ai.R;

/**
 * 首次启动的全屏引导页：三张整屏插图，左滑三次进入主页面。
 * <ul>
 *   <li>第 1 张：远程查看 Linux 设备详情、异常监控、多设备管理</li>
 *   <li>第 2 张：远程查看摄像头画面，控制电机 / 舵机等外部设备</li>
 *   <li>第 3 张：Agent 帮你管理设备，下发任务，专属智能机器人</li>
 * </ul>
 * 第 3 页继续左滑（或点「开始使用」/「跳过」）即进入 {@link MainActivity}，
 * 并写入「已看过」标记，之后启动不再展示；设置页「功能导览」可重新打开。
 */
public class OnboardingActivity extends AppCompatActivity {

    /** true = 从设置页的「功能导览」进入，结束时回到原来的页面而不是再开一个主页面。 */
    public static final String EXTRA_REPLAY = "replay";

    private static final int DOT_ACTIVE_WIDTH_DP = 22;
    private static final int DOT_IDLE_WIDTH_DP = 6;

    private static final class Page {
        final int imageRes;
        final int a11yRes;
        final int eyebrowRes;
        final int titleRes;
        final int descRes;
        final int accentRes;
        final int ctaStart;
        final int ctaEnd;

        Page(int imageRes, int a11yRes, int eyebrowRes, int titleRes, int descRes,
             int accentRes, int ctaStart, int ctaEnd) {
            this.imageRes = imageRes;
            this.a11yRes = a11yRes;
            this.eyebrowRes = eyebrowRes;
            this.titleRes = titleRes;
            this.descRes = descRes;
            this.accentRes = accentRes;
            this.ctaStart = ctaStart;
            this.ctaEnd = ctaEnd;
        }
    }

    private static final Page[] PAGES = new Page[]{
            new Page(R.drawable.onboarding_1, R.string.onboarding_a11y_1,
                    R.string.onboarding_eyebrow_1, R.string.onboarding_title_1,
                    R.string.onboarding_desc_1, R.color.onboarding_accent_1,
                    0xFF2563EB, 0xFF22D3EE),
            new Page(R.drawable.onboarding_2, R.string.onboarding_a11y_2,
                    R.string.onboarding_eyebrow_2, R.string.onboarding_title_2,
                    R.string.onboarding_desc_2, R.color.onboarding_accent_2,
                    0xFF0D9488, 0xFF34D399),
            new Page(R.drawable.onboarding_3, R.string.onboarding_a11y_3,
                    R.string.onboarding_eyebrow_3, R.string.onboarding_title_3,
                    R.string.onboarding_desc_3, R.color.onboarding_accent_3,
                    0xFF7C3AED, 0xFFEC4899),
    };

    private ViewPager2 pager;
    private PageAdapter adapter;
    private LinearLayout dots;
    private TextView tvEyebrow, tvTitle, tvDesc, tvSwipeHint, btnNext;
    private View btnSkip;

    private final List<ValueAnimator> dotAnimators = new ArrayList<>();

    private boolean replay;
    private boolean entering;
    private boolean boundOnce;

    /** 第 3 页左滑手势状态。 */
    private float downX, downY;
    private boolean draggingPage3;
    private boolean enteringBySwipe;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        UiUtil.lockPortrait(this);
        applyEdgeToEdge();
        setContentView(R.layout.activity_onboarding);

        replay = getIntent().getBooleanExtra(EXTRA_REPLAY, false);

        pager = findViewById(R.id.onboardingPager);
        dots = findViewById(R.id.dots);
        tvEyebrow = findViewById(R.id.tvEyebrow);
        tvTitle = findViewById(R.id.tvTitle);
        tvDesc = findViewById(R.id.tvDesc);
        tvSwipeHint = findViewById(R.id.tvSwipeHint);
        btnNext = findViewById(R.id.btnNext);
        btnSkip = findViewById(R.id.btnSkip);

        applyInsets();
        buildDots();

        adapter = new PageAdapter(this, PAGES);
        pager.setAdapter(adapter);
        pager.setOffscreenPageLimit(1);
        pager.registerOnPageChangeCallback(new ViewPager2.OnPageChangeCallback() {
            @Override
            public void onPageSelected(int position) {
                if (position != PAGES.length - 1) resetPage3Transform();
                bindPage(position, true);
            }

            @Override
            public void onPageScrollStateChanged(int state) {
                if (state == ViewPager2.SCROLL_STATE_IDLE && !entering
                        && pager.getCurrentItem() != PAGES.length - 1) {
                    resetPage3Transform();
                }
            }
        });

        btnNext.setOnClickListener(v -> {
            int cur = pager.getCurrentItem();
            if (cur < PAGES.length - 1) {
                pager.setCurrentItem(cur + 1, true);
            } else {
                enterApp();
            }
        });
        btnSkip.setOnClickListener(v -> enterApp());

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (entering) return;
                int cur = pager.getCurrentItem();
                if (cur > 0) {
                    pager.setCurrentItem(cur - 1, true);
                } else {
                    // 第一次启动时直接退出 App（下次启动会再次展示导览）
                    finish();
                    overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out);
                }
            }
        });

        bindPage(0, true);
        attachPage3SwipeOut();
    }

    // ---------------------------------------------------------------- 全屏

    private void applyEdgeToEdge() {
        // 系统栏透明由主题统一处理；API 35+ 起 setStatusBarColor/setNavigationBarColor 已是空操作
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            getWindow().setStatusBarContrastEnforced(false);
            getWindow().setNavigationBarContrastEnforced(false);
        }
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(
                getWindow(), getWindow().getDecorView());
        if (controller != null) {
            // 深色插图上用浅色系统栏图标
            controller.setAppearanceLightStatusBars(false);
            controller.setAppearanceLightNavigationBars(false);
        }
    }

    private void applyInsets() {
        final View root = findViewById(R.id.onboardingRoot);
        final View content = findViewById(R.id.onboardingContent);
        final int skipTop = dp(10);
        final int skipEnd = dp(18);
        final int side = dp(26);
        final int top = dp(8);
        final int bottom = dp(18);
        ViewCompat.setOnApplyWindowInsetsListener(root, (v, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars());
            ViewGroup.MarginLayoutParams lp =
                    (ViewGroup.MarginLayoutParams) btnSkip.getLayoutParams();
            lp.topMargin = skipTop + bars.top;
            lp.setMarginEnd(skipEnd + bars.right);
            btnSkip.setLayoutParams(lp);
            content.setPaddingRelative(side + bars.left, top, side + bars.right,
                    bottom + bars.bottom);
            return insets;
        });
        ViewCompat.requestApplyInsets(root);
    }

    // ---------------------------------------------------------------- 文案 / 指示点

    private void bindPage(int position, boolean animate) {
        Page page = PAGES[position];
        int accent = ContextCompat.getColor(this, page.accentRes);
        tvEyebrow.setText(page.eyebrowRes);
        tvEyebrow.setTextColor(accent);
        tvTitle.setText(page.titleRes);
        tvDesc.setText(page.descRes);
        boolean last = position == PAGES.length - 1;
        btnNext.setText(last ? R.string.onboarding_start : R.string.onboarding_next);
        tvSwipeHint.setVisibility(last ? View.VISIBLE : View.INVISIBLE);
        applyCta(page);
        updateDots(position, animate);
        pager.setContentDescription(getString(R.string.onboarding_page_of, position + 1));
        if (animate) {
            animateTextIn();
        } else {
            boundOnce = true;
        }
    }

    private void animateTextIn() {
        float offset = dp(10);
        View[] views = new View[]{tvEyebrow, tvTitle, tvDesc};
        for (View v : views) {
            v.animate().cancel();
            v.setAlpha(0f);
            v.setTranslationY(offset);
            v.animate()
                    .alpha(1f)
                    .translationY(0f)
                    .setDuration(320)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
        }
        if (boundOnce) {
            btnNext.setScaleX(0.97f);
            btnNext.setScaleY(0.97f);
            btnNext.animate().scaleX(1f).scaleY(1f)
                    .setDuration(260)
                    .setInterpolator(new DecelerateInterpolator())
                    .start();
        }
        boundOnce = true;
    }

    private void applyCta(Page page) {
        GradientDrawable gradient = new GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{page.ctaStart, page.ctaEnd});
        gradient.setCornerRadius(dp(27));
        RippleDrawable ripple = new RippleDrawable(
                ColorStateList.valueOf(0x40FFFFFF), gradient, null);
        btnNext.setBackground(ripple);
        btnNext.setElevation(dp(10));
    }

    private void buildDots() {
        for (int i = 0; i < PAGES.length; i++) {
            View dot = new View(this);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(6), dp(6));
            lp.setMarginStart(dp(3));
            lp.setMarginEnd(dp(3));
            dot.setBackground(pill(0x59FFFFFF));
            dot.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            dots.addView(dot, lp);
        }
        updateDots(0, false);
    }

    private void updateDots(int active, boolean animate) {
        if (dots == null || dots.getChildCount() == 0) return;
        for (ValueAnimator a : dotAnimators) a.cancel();
        dotAnimators.clear();
        int accent = ContextCompat.getColor(this, PAGES[active].accentRes);
        for (int i = 0; i < dots.getChildCount(); i++) {
            final View dot = dots.getChildAt(i);
            final LinearLayout.LayoutParams lp =
                    (LinearLayout.LayoutParams) dot.getLayoutParams();
            int target = dp(i == active ? DOT_ACTIVE_WIDTH_DP : DOT_IDLE_WIDTH_DP);
            int from = lp.width <= 0 ? target : lp.width;
            GradientDrawable bg = dot.getBackground() instanceof GradientDrawable
                    ? (GradientDrawable) dot.getBackground() : null;
            if (bg != null) bg.setColor(i == active ? accent : 0x59FFFFFF);
            if (!animate || from == target) {
                lp.width = target;
                dot.setLayoutParams(lp);
                continue;
            }
            ValueAnimator anim = ValueAnimator.ofInt(from, target);
            anim.setDuration(220);
            anim.setInterpolator(new DecelerateInterpolator());
            anim.addUpdateListener(a -> {
                lp.width = (int) a.getAnimatedValue();
                dot.setLayoutParams(lp);
            });
            anim.start();
            dotAnimators.add(anim);
        }
    }

    // ---------------------------------------------------------------- 第三次左滑进入主页面

    /**
     * ViewPager2 到最后一页后不能再往左翻，这里在最后一页接管「继续左滑」手势：
     * 页面跟手左移，松手时越过阈值就直接滑出并进入主页面，否则弹回。
     */
    private void attachPage3SwipeOut() {
        pager.post(() -> {
            if (isFinishing() || pager.getChildCount() == 0) return;
            View child = pager.getChildAt(0);
            if (!(child instanceof RecyclerView)) return;
            ((RecyclerView) child).setOnTouchListener((v, event) -> {
                onPagerTouch(event);
                return false; // 不消费事件，正常翻页（右滑回上一页）仍然交给 ViewPager2
            });
        });
    }

    private void onPagerTouch(MotionEvent event) {
        if (entering || adapter == null) return;
        if (pager.getCurrentItem() != PAGES.length - 1) {
            draggingPage3 = false;
            return;
        }
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = event.getX();
                downY = event.getY();
                draggingPage3 = false;
                enteringBySwipe = false;
                break;
            case MotionEvent.ACTION_MOVE: {
                float dx = event.getX() - downX;
                float dy = event.getY() - downY;
                if (!draggingPage3 && dx < -dp(10)
                        && Math.abs(dx) > Math.abs(dy) * 1.25f) {
                    draggingPage3 = true;
                }
                if (draggingPage3) {
                    View page = adapter.pageView();
                    if (page != null) {
                        if (dx < 0) {
                            page.setTranslationX(dx * 0.55f);
                            page.setAlpha(1f + Math.max(-0.35f,
                                    dx / Math.max(1f, pager.getWidth()) * 1.2f));
                        } else {
                            page.setTranslationX(0f);
                            page.setAlpha(1f);
                        }
                    }
                }
                break;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL: {
                boolean wasDragging = draggingPage3;
                draggingPage3 = false;
                if (!wasDragging) break;
                float dx = event.getX() - downX;
                boolean shouldEnter = event.getActionMasked() == MotionEvent.ACTION_UP
                        && dx < -Math.max(dp(64), pager.getWidth() * 0.16f);
                if (shouldEnter) {
                    enteringBySwipe = true;
                    enterApp();
                } else {
                    animatePage3Back();
                }
                break;
            }
            default:
                break;
        }
    }

    private void animatePage3Back() {
        View page = adapter == null ? null : adapter.pageView();
        if (page == null) return;
        page.animate().translationX(0f).alpha(1f)
                .setDuration(220)
                .setInterpolator(new DecelerateInterpolator())
                .start();
    }

    private void resetPage3Transform() {
        View page = adapter == null ? null : adapter.pageView();
        if (page == null || entering) return;
        page.animate().cancel();
        page.setTranslationX(0f);
        page.setAlpha(1f);
    }

    // ---------------------------------------------------------------- 进入应用

    private void enterApp() {
        if (entering) return;
        entering = true;
        pager.setUserInputEnabled(false);
        btnNext.setEnabled(false);
        btnSkip.setEnabled(false);
        OnboardingPrefs.markDone(this);

        View page = adapter == null ? null : adapter.pageView();
        Runnable done = this::finishIntoApp;
        if (page != null) {
            page.animate()
                    .translationX(-pager.getWidth() * (enteringBySwipe ? 1.1f : 0.9f))
                    .alpha(0.15f)
                    .setDuration(260)
                    .setInterpolator(new DecelerateInterpolator())
                    .withEndAction(done)
                    .start();
        } else {
            done.run();
        }
    }

    private void finishIntoApp() {
        if (isFinishing()) return;
        if (replay) {
            finish();
            overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out);
            return;
        }
        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        startActivity(intent);
        finish();
        overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out);
    }

    @Override
    protected void onDestroy() {
        for (ValueAnimator a : dotAnimators) a.cancel();
        dotAnimators.clear();
        if (adapter != null) adapter.release();
        super.onDestroy();
    }

    // ---------------------------------------------------------------- 工具

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private GradientDrawable pill(int color) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setShape(GradientDrawable.RECTANGLE);
        drawable.setCornerRadius(dp(3));
        drawable.setColor(color);
        return drawable;
    }

    // ---------------------------------------------------------------- 适配器

    private static final class PageAdapter extends RecyclerView.Adapter<PageAdapter.VH> {

        private final LayoutInflater inflater;
        private final Page[] pages;
        private final String[] a11y;
        /** 整屏插图按需要解码（按尺寸 + GPU/565 + 后台线程），避免三张 12 MB 位图同时驻留堆内存。 */
        private final OnboardingImageLoader images;
        private View page3View;

        PageAdapter(OnboardingActivity activity, Page[] pages) {
            this.inflater = LayoutInflater.from(activity);
            this.pages = pages;
            this.images = new OnboardingImageLoader(activity.getResources());
            this.a11y = new String[pages.length];
            for (int i = 0; i < pages.length; i++) {
                a11y[i] = activity.getString(pages[i].a11yRes);
            }
        }

        @NonNull
        @Override
        public VH onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            return new VH(inflater.inflate(R.layout.item_onboarding_page, parent, false));
        }

        @Override
        public void onBindViewHolder(@NonNull VH holder, int position) {
            Page page = pages[position];
            images.load(holder.image, page.imageRes);
            holder.image.setContentDescription(a11y[position]);
            // 复用 / 回到该页时清掉上一次左滑留下的位移
            holder.itemView.setTranslationX(0f);
            holder.itemView.setAlpha(1f);
            if (position == pages.length - 1) {
                page3View = holder.itemView;
            }
        }

        @Override
        public void onViewRecycled(@NonNull VH holder) {
            holder.image.setImageDrawable(null);
            holder.image.setTag(R.id.ivPage, null);
        }

        void release() {
            images.shutdown();
        }

        @Override
        public int getItemCount() {
            return pages.length;
        }

        View pageView() {
            return page3View;
        }

        static final class VH extends RecyclerView.ViewHolder {
            final ImageView image;

            VH(@NonNull View itemView) {
                super(itemView);
                image = itemView.findViewById(R.id.ivPage);
            }
        }
    }
}
