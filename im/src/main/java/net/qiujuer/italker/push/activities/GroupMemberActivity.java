package net.qiujuer.italker.push.activities;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.widget.TextView;

import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.bumptech.glide.Glide;
import com.walle.base.BaseApp;

import net.qiujuer.italker.push.R;
import net.qiujuer.italker.push.R2;

import net.qiujuer.italker.common.app.PresenterToolbarActivity;
import net.qiujuer.italker.common.widget.PortraitView;
import net.qiujuer.italker.common.widget.recycler.RecyclerAdapter;
import net.qiujuer.italker.factory.model.db.view.MemberUserModel;
import net.qiujuer.italker.factory.presenter.group.GroupMembersContract;
import net.qiujuer.italker.factory.presenter.group.GroupMembersPresenter;
import net.qiujuer.italker.push.frags.group.GroupMemberAddFragment;
import net.qiujuer.italker.push.frags.group.GroupMemberDelFragment;

import java.util.List;

import butterknife.BindView;
import butterknife.OnClick;

public class GroupMemberActivity extends PresenterToolbarActivity<GroupMembersContract.Presenter>
        implements GroupMembersContract.View, GroupMemberAddFragment.Callback {
    private static final String KEY_GROUP_ID = "KEY_GROUP_ID";
    private static final String KEY_GROUP_ADMIN = "KEY_GROUP_ADMIN";


    @BindView(R2.id.recycler)
    RecyclerView mRecycler;

    @BindView(R2.id.tv_add)
    TextView mTvAdd;
    @BindView(R2.id.tv_del)
    TextView mTvDel;
    @BindView(R2.id.tv_quit)
    TextView mTvQuit;


    private String mGroupId;
    private boolean mIsAdmin;
    private RecyclerAdapter<MemberUserModel> mAdapter;


    public static void show(Context context, String groupId) {
        show(context, groupId, false);
    }

    public static void showAdmin(Context context, String groupId) {
        show(context, groupId, true);
    }

    private static void show(Context context, String groupId, boolean isAdmin) {
        if (TextUtils.isEmpty(groupId))
            return;

        Intent intent = new Intent(context, GroupMemberActivity.class);
        intent.putExtra(KEY_GROUP_ID, groupId);
        intent.putExtra(KEY_GROUP_ADMIN, isAdmin);
        context.startActivity(intent);

    }


    @Override
    protected int getContentLayoutId() {
        return R.layout.activity_group_member;
    }

    @Override
    protected boolean initArgs(Bundle bundle) {
        mGroupId = bundle.getString(KEY_GROUP_ID);
        mIsAdmin = bundle.getBoolean(KEY_GROUP_ADMIN);
        return !TextUtils.isEmpty(mGroupId);
    }

    @Override
    protected void initWidget() {
        super.initWidget();

        setTitle(R.string.title_member_list);

        mRecycler.setLayoutManager(new LinearLayoutManager(this));
        mRecycler.setAdapter(mAdapter = new RecyclerAdapter<MemberUserModel>() {
            @Override
            protected int getItemViewType(int position, MemberUserModel memberUserModel) {
                return R.layout.cell_group_create_contact;
            }

            @Override
            protected ViewHolder<MemberUserModel> onCreateViewHolder(View root, int viewType) {
                return new GroupMemberActivity.ViewHolder(root);
            }
        });

    }

    @Override
    protected void initData() {
        super.initData();
        // 开始数据刷新
        mPresenter.refresh();


        if (mIsAdmin) {
            // 管理员权限，只能添加和删除成员
            mTvQuit.setVisibility(View.GONE);
        }else {
            // 普通权限，只能退出群
            mTvAdd.setVisibility(View.GONE);
            mTvDel.setVisibility(View.GONE);
        }
    }

    @Override
    public RecyclerAdapter<MemberUserModel> getRecyclerAdapter() {
        return mAdapter;
    }

    @Override
    public void onAdapterDataChanged() {
        // 隐藏Loading就好
        hideLoading();
    }

    @Override
    protected GroupMembersContract.Presenter initPresenter() {
        return new GroupMembersPresenter(this);
    }

    @Override
    public String getGroupId() {
        return mGroupId;
    }

    @Override
    public void exit() {
        for(Activity activity : BaseApp.getActivities()){
            if(!(activity instanceof ImMainActivity)){
                activity.finish();
            }
        }
    }

    @Override
    public void hideLoading() {
        super.hideLoading();
    }

    @Override
    public void refreshMembers() {
        // 重新加载成员信息
        if (mPresenter != null)
            mPresenter.refresh();
    }

    class ViewHolder extends RecyclerAdapter.ViewHolder<MemberUserModel> {
        @BindView(R2.id.im_portrait)
        PortraitView mPortrait;
        @BindView(R2.id.txt_name)
        TextView mName;


        ViewHolder(View itemView) {
            super(itemView);
            itemView.findViewById(R.id.cb_select).setVisibility(View.GONE);
        }

        @Override
        protected void onBind(MemberUserModel model) {
            mPortrait.setup(Glide.with(GroupMemberActivity.this), model.portrait);
            mName.setText(model.name);
        }

        @OnClick(R2.id.im_portrait)
        void onPortraitClick() {
            PersonalActivity.show(GroupMemberActivity.this, mData.userId);
        }
    }

    @OnClick(R2.id.tv_add)
    public void onClickAdd(View v){
        new GroupMemberAddFragment()
                .show(getSupportFragmentManager(), GroupMemberAddFragment.class.getName());
    }

    @OnClick(R2.id.tv_del)
    public void onClickDel(View v){
        new GroupMemberDelFragment()
                .show(getSupportFragmentManager(), GroupMemberDelFragment.class.getName());
    }

    @OnClick(R2.id.tv_quit)
    public void onClickQuit(View v){
        // 开始数据刷新
        mPresenter.quit();
    }
}
