package net.qiujuer.italker.common.app;

import android.content.Context;
import android.os.Bundle;
import android.util.Log;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;

import androidx.annotation.LayoutRes;
import androidx.annotation.Nullable;

import net.qiujuer.italker.common.widget.convention.PlaceHolderView;

import butterknife.ButterKnife;
import butterknife.Unbinder;

/**
 * @author qiujuer
 */

public abstract class Fragment extends androidx.fragment.app.Fragment {
    protected View mRoot;
    protected Unbinder mRootUnBinder;
    protected PlaceHolderView mPlaceHolderView;    // 占位布局的作用？？？？？？？？？？？？？？
    // 标示是否第一次初始化数据
    protected boolean mIsFirstInitData = true;
    private String tag = null;

    /**
     * 1.1 当碎片和活动建立关联的时候调用
     *
     * @param context
     */
    @Override
    public void onAttach(Context context) {
        super.onAttach(context);

        // 初始化参数
        initArgs(getArguments());
        tag = this.getClass().toString() + "__Fragment";
        Log.d(tag,"onAttach");
    }

    /**
     * 1.2 保持数据
     * @param savedInstanceState
     * 原本没有，我加的，
     */
    @Override
    public void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Log.d(tag,"onCreate");
    }

    /**
     * 1.3 为碎片创建视图（加载布局）的时候调用
     * @param inflater
     * @param container
     * @param savedInstanceState
     * @return
     */
    @Nullable
    @Override
    public View onCreateView(LayoutInflater inflater, @Nullable ViewGroup container, @Nullable Bundle savedInstanceState) {
        if (mRoot == null) {
            int layId = getContentLayoutId();
            // 初始化当前的跟布局，但是不在创建时就添加到container里边
            View root = inflater.inflate(layId, container, false);
            initWidget(root);
            mRoot = root;
        } else {
            if (mRoot.getParent() != null) {
                // 把当前Root从其父控件中移除
                ((ViewGroup) mRoot.getParent()).removeView(mRoot);
            }
        }
        Log.d(tag,"onCreateView");

        return mRoot;
    }

    /**
     * 在 onCreateView 函数后才执行，网上也没有说明白这个函数的作用，我猜这个函数的作用是，因为 onCreateView 就是为Fragment 创建view的，
     * 那么可以在这个函数里处理View相关的工作，因为这个时候View以及创建好了。
     * @param view
     * @param savedInstanceState
     */
    @Override
    public void onViewCreated(View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        if (mIsFirstInitData) {
            // 触发一次以后就不会触发
            mIsFirstInitData = false;
            // 触发
            onFirstInit();
        }

        // 当View创建完成后初始化数据
        initData();

        Log.d(tag,"onViewCreated");
    }

    /**
     * 1.4
     * @param savedInstanceState
     */
    @Override
    public void onActivityCreated(@Nullable Bundle savedInstanceState) {
        super.onActivityCreated(savedInstanceState);
        Log.d(tag,"onActivityCreated");
    }

    /**
     * 1.5
     */
    @Override
    public void onStart() {
        super.onStart();
        Log.d(tag,"onStart");
    }

    /**
     * 1.6
     */
    @Override
    public void onResume() {
        super.onResume();
        Log.d(tag,"onResume");
    }

    /**
     * 0.5
     */
    @Override
    public void onPause() {
        super.onPause();
        Log.d(tag,"onPause");
    }

    /**
     * 0.4
     */
    @Override
    public void onStop() {
        super.onStop();
        Log.d(tag,"onStop");
    }

    /**
     * 0.3
     */
    @Override
    public void onDestroyView() {
        super.onDestroyView();
        Log.d(tag,"onDestroyView");
    }

    /**
     * 0.2
     */
    @Override
    public void onDestroy() {
        super.onDestroy();
        Log.d(tag,"onDestroy");
    }

    /**
     * 0.1
     */
    @Override
    public void onDetach() {
        super.onDetach();
        Log.d(tag,"onDetach");
    }

    /**
     * 初始化相关参数
     */
    protected void initArgs(Bundle bundle) {

    }

    /**
     * 得到当前界面的资源文件Id
     *
     * @return 资源文件Id
     */
    @LayoutRes
    protected abstract int getContentLayoutId();

    /**
     * 初始化控件
     */
    protected void initWidget(View root) {
        mRootUnBinder = ButterKnife.bind(this, root);  //这个绑定操作，是每个 Activity 和 Fragment 都需要有的。
    }

    /**
     * 初始化数据
     */
    protected void initData() {

    }

    /**
     * 当首次初始化数据的时候会调用的方法，
     * gs：这个函数是在每次fragment创建完毕后，被调用。
     */
    protected void onFirstInit() {

    }

    /**
     * 返回按键触发时调用
     *
     * @return 返回True代表我已处理返回逻辑，Activity不用自己finish。
     * 返回False代表我没有处理逻辑，Activity自己走自己的逻辑
     */
    public boolean onBackPressed() {
        return false;
    }


    /**
     * 设置占位布局
     *
     * @param placeHolderView 继承了占位布局规范的View
     */
    public void setPlaceHolderView(PlaceHolderView placeHolderView) {
        this.mPlaceHolderView = placeHolderView;
    }

}
