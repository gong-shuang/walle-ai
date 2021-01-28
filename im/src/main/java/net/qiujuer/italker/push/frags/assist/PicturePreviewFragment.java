package net.qiujuer.italker.push.frags.assist;

import android.app.Dialog;
import android.content.DialogInterface;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;

import androidx.annotation.NonNull;

import com.bumptech.glide.Glide;
import com.google.android.material.bottomsheet.BottomSheetDialog;
import com.google.android.material.bottomsheet.BottomSheetDialogFragment;

import net.qiujuer.italker.push.R;

import java.util.Objects;

public class PicturePreviewFragment extends BottomSheetDialogFragment {

    private String url;

    public PicturePreviewFragment() {
        // Required empty public constructor
    }

    @NonNull
    @Override
    public Dialog onCreateDialog(Bundle savedInstanceState) {
        // 复用即可
        return new BottomSheetDialog(Objects.requireNonNull(getContext()));
    }

    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, ViewGroup container,
                             Bundle savedInstanceState) {
        // 获取布局中的控件
        View root = inflater.inflate(R.layout.fragment_picture_preview, container, false);

        // 找到按钮
        root.findViewById(R.id.tv_close)
                .setOnClickListener(v -> {
                    dismiss();
                });

        ImageView imageView = root.findViewById(R.id.iv_image);
        Glide.with(getContext()).load(url).into(imageView);

        return root;
    }

    public PicturePreviewFragment setUrl(String url) {
        this.url = url;
        return this;
    }
}
