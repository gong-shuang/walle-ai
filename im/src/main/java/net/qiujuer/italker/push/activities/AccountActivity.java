package net.qiujuer.italker.push.activities;

import android.content.Context;
import android.content.Intent;
import android.graphics.PorterDuff;
import android.graphics.drawable.Drawable;
import android.widget.ImageView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.graphics.drawable.DrawableCompat;

import com.bumptech.glide.Glide;
import com.bumptech.glide.request.target.CustomTarget;
import com.bumptech.glide.request.target.ViewTarget;
import com.bumptech.glide.request.transition.Transition;

import net.qiujuer.italker.push.R;
import net.qiujuer.italker.push.R2;

import net.qiujuer.genius.ui.compat.UiCompat;
import net.qiujuer.italker.common.app.Activity;
import net.qiujuer.italker.common.app.Fragment;
import net.qiujuer.italker.push.frags.account.AccountTrigger;
import net.qiujuer.italker.push.frags.account.LoginFragment;
import net.qiujuer.italker.push.frags.account.RegisterFragment;

import butterknife.BindView;

/**
 * Activity 只是一个架子，一个容器，里面没有什么内容，只有一个装 Fragment 的 FrameLayout 布局。
 * 具体的内容在布局显示出来。
 */
public class AccountActivity extends Activity implements AccountTrigger {
    private Fragment mCurFragment;  //当前的Fragment，要么是Login，要么是Register
    private Fragment mLoginFragment;
    private Fragment mRegisterFragment;

    @BindView(R2.id.im_bg)
    ImageView mBg;

    /**
     * 账户Activity显示的入口
     *
     * @param context Context
     */
    public static void show(Context context) {
        context.startActivity(new Intent(context, AccountActivity.class));
    }

    @Override
    protected int getContentLayoutId() {
        return R.layout.activity_account;
    }

    @Override
    protected void initWidget() {
        super.initWidget();

        // 初始化Fragment
        mCurFragment = mLoginFragment = new LoginFragment();
        //下面的代码，是动态加载布局的固定代码
        getSupportFragmentManager()   // 获取FragmentManager
                .beginTransaction()   // 创建一个事务
                .add(R.id.lay_container, mCurFragment)    // 向容器中添加碎片
                .commit();    // 提交事务

        // 初始化背景
        Glide.with(this)
                .load(R.drawable.bg_src_tianjin)
                .centerCrop() //居中剪切
                .into(new CustomTarget<Drawable>() {
                    @Override
                    public void onResourceReady(@NonNull Drawable resource, @Nullable Transition<? super Drawable> transition) {
                        // 拿到glide的Drawable
                        Drawable drawable = resource.getCurrent();
                        // 使用适配类进行包装
                        drawable = DrawableCompat.wrap(drawable);
                        drawable.setColorFilter(UiCompat.getColor(getResources(), R.color.colorAccent),
                                PorterDuff.Mode.SCREEN); // 设置着色的效果和颜色，蒙板模式
                        // 设置给ImageView
//                        this.view.setImageDrawable(drawable);
                    }

                    @Override
                    public void onLoadCleared(@Nullable Drawable placeholder) {

                    }
                });
    }

    /**
     * 触发器，意思是，如果是登陆界面就切换为注册界面，如果是注册界面，就切换成登陆界面。
     */
    @Override
    public void triggerView() {
        Fragment fragment;
        if (mCurFragment == mLoginFragment) {
            //如果是登陆界面
            if (mRegisterFragment == null) {
                //默认情况下为null，
                //第一次之后就不为null了
                mRegisterFragment = new RegisterFragment();
            }
            fragment = mRegisterFragment;
        } else {
            // 如果是注册界面
            // 因为默认请求下mLoginFragment已经赋值，无须判断null
            fragment = mLoginFragment;
        }

        // 重新赋值当前正在显示的Fragment
        mCurFragment = fragment;
        // 切换显示
        getSupportFragmentManager().beginTransaction()
                .replace(R.id.lay_container, fragment)
                .commit();
    }
}
