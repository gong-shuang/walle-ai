package net.qiujuer.italker.push.frags.main;


import android.content.Intent;
import android.text.Spannable;
import android.text.SpannableString;
import android.text.TextUtils;
import android.util.Log;
import android.view.View;
import android.widget.TextView;

import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.bumptech.glide.Glide;

import net.qiujuer.italker.common.app.PresenterFragment;
import net.qiujuer.italker.common.widget.EmptyView;
import net.qiujuer.italker.common.widget.PortraitView;
import net.qiujuer.italker.common.widget.recycler.RecyclerAdapter;
import net.qiujuer.italker.face.Face;
import net.qiujuer.italker.factory.model.db.Session;
import net.qiujuer.italker.factory.presenter.message.SessionContract;
import net.qiujuer.italker.factory.presenter.message.SessionPresenter;
import net.qiujuer.italker.push.R;
import net.qiujuer.italker.push.R2;
import net.qiujuer.italker.push.activities.MessageActivity;
import net.qiujuer.italker.utils.DateTimeUtil;

import butterknife.BindView;

/**
 * 这个Fragment，是第一个主界面的Fragment（就是聊天的界面），准确的说，名字应该叫MessageFragment
 */
public class ActiveFragment extends PresenterFragment<SessionContract.Presenter>
        implements SessionContract.View {

    @BindView(R2.id.empty)
    EmptyView mEmptyView;

    @BindView(R2.id.recycler)
    RecyclerView mRecycler;

    ActiveFragment activeFragment;

    // 适配器，User，可以直接从数据库查询数据
    private RecyclerAdapter<Session> mAdapter;


    public ActiveFragment() {
        // Required empty public constructor
    }


    @Override
    protected int getContentLayoutId() {
        activeFragment = this;
        return R.layout.fragment_active;
    }

    @Override
    protected void initWidget(View root) {
        super.initWidget(root);

        // 初始化Recycler
        mRecycler.setLayoutManager(new LinearLayoutManager(getContext()));
        mRecycler.setAdapter(mAdapter = new RecyclerAdapter<Session>() {    // 这里创建的子Recycler，没有设置点击事件监听
            @Override
            protected int getItemViewType(int position, Session session) {
                // 返回cell的布局id
                return R.layout.cell_chat_list;  // 就是 Item 的布局，就是下面定义的ViewHolder中的布局。
            }

            @Override
            protected ViewHolder<Session> onCreateViewHolder(View root, int viewType) {
                return new ActiveFragment.ViewHolder(root);
            }
        });

        // 点击事件监听，只监听了点击事件，没有监听长按事件
        mAdapter.setListener(new RecyclerAdapter.AdapterListenerImpl<Session>() {
            @Override
            public void onItemClick(RecyclerAdapter.ViewHolder holder, Session session) {
                // 跳转到聊天界面
                MessageActivity.show(getContext(), session, activeFragment);
            }
        });

        // 初始化占位布局
        mEmptyView.bind(mRecycler);    // 这个占位布局的作用是什么？
        setPlaceHolderView(mEmptyView);
    }

    @Override
    public void onActivityResult(int requestCode, int resultCode, Intent data) {
        switch (requestCode){
            case MessageActivity.USER:
                if(resultCode  == MessageActivity.RESULT_OK){
                    String re_data = data.getStringExtra(MessageActivity.KEY_RECEIVER_ID);
                    Log.d("333","re_data:" + re_data);
                }
        }
    }

    @Override
    protected void onFirstInit() {
        super.onFirstInit();
        // 进行一次数据加载
        mPresenter.start();    // 这个一步非常重要，说明初始化已经完成了，对象都已经创建好了，这是需要开始工作了。
    }

//    /**
//     * gs add,for solve sort
//     */
//    @Override
//    public void onStart() {
//        super.onStart();
//        // 界面可见的时候，进行一次数据加载
//        mPresenter.start();
//    }

    /**
     * 这个是关键，创建 Presenter
     * @return
     */
    @Override
    protected SessionContract.Presenter initPresenter() {
        return new SessionPresenter(this);
    }

    @Override
    public RecyclerAdapter<Session> getRecyclerAdapter() {
        return mAdapter;
    }

    @Override
    public void onAdapterDataChanged() {
        mPlaceHolderView.triggerOkOrEmpty(mAdapter.getItemCount() > 0);
    }

    // 界面数据渲染，这里的ViewHolder是RecyclerView的子item。
    class ViewHolder extends RecyclerAdapter.ViewHolder<Session> {
        @BindView(R2.id.im_portrait)
        PortraitView mPortraitView;

        @BindView(R2.id.txt_name)
        TextView mName;

        @BindView(R2.id.txt_content)
        TextView mContent;

        @BindView(R2.id.txt_time)
        TextView mTime;

        ViewHolder(View itemView) {
            super(itemView);
        }

        /**
         *
         * @param session
         */
        @Override
        protected void onBind(Session session) {
            mPortraitView.setup(Glide.with(ActiveFragment.this), session.getPicture());
            mName.setText(session.getTitle());

            String str = TextUtils.isEmpty(session.getContent()) ? "" : session.getContent();
            Spannable spannable = new SpannableString(str);
            // 解析表情
            Face.decode(mContent, spannable, (int) mContent.getTextSize());
            // 把内容设置到布局上
            mContent.setText(spannable);

            mTime.setText(DateTimeUtil.getSampleDate(session.getModifyAt()));
        }
    }
}
