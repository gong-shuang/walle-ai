package com.walle.open.push;

import androidx.annotation.StringRes;
import androidx.appcompat.app.AppCompatActivity;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.telephony.TelephonyManager;
import android.text.TextUtils;
import android.view.View;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import com.huawei.agconnect.core.BuildConfig;
import com.huawei.gspush.R;
import com.mpush.api.Constants;
import com.mpush.api.http.HttpCallback;
import com.mpush.api.http.HttpMethod;
import com.mpush.api.http.HttpRequest;
import com.mpush.api.http.HttpResponse;
import com.mpush.client.ClientConfig;
import com.walle.open.eventBus.MessageEvent;
import com.walle.push.PushConstant;
import com.walle.push.mpush.MPush;
import com.walle.push.mpush.MPushInit;
import com.walle.push.mpush.MyLog;

import org.greenrobot.eventbus.EventBus;
import org.greenrobot.eventbus.Subscribe;
import org.greenrobot.eventbus.ThreadMode;
import org.json.JSONObject;

import java.util.concurrent.TimeUnit;

public class PushTestActivity extends AppCompatActivity {

    TextView mLog;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_push_test);

        mLog = (TextView) findViewById(R.id.log);

    }

    public void bindUser(View btn) {
        EditText et = (EditText) findViewById(R.id.from);
        String userId = et.getText().toString().trim();
        if (!TextUtils.isEmpty(userId)) {
            MPush.I.bindAccount(userId, "mpush:" + (int) (Math.random() * 10));
        }
    }

    public void startPush(View btn) {

        EditText etUser = (EditText) findViewById(R.id.from);
        String userId = etUser.getText().toString().trim();

        if(TextUtils.isEmpty(userId)){
            Toast.makeText(this, "start push", Toast.LENGTH_SHORT).show();
        }

        //初始化 mpush
        MPushInit.getInstance().start(userId);

        Toast.makeText(this, "start push", Toast.LENGTH_SHORT).show();
    }

    public void sendPush(View btn) throws Exception {
        String allocServer = PushConstant.allocServer;

        if (TextUtils.isEmpty(allocServer)) {
            Toast.makeText(this, "请填写正确的alloc地址", Toast.LENGTH_SHORT).show();
            return;
        }

        if (!allocServer.startsWith("http://")) {
            allocServer = "http://" + allocServer;
        }

        EditText toET = (EditText) findViewById(R.id.to);
        String to = toET.getText().toString().trim();

        EditText fromET = (EditText) findViewById(R.id.from);
        String from = fromET.getText().toString().trim();

        EditText helloET = (EditText) findViewById(R.id.httpProxy);
        String hello = helloET.getText().toString().trim();

        if (TextUtils.isEmpty(hello)) hello = "hello";

        JSONObject params = new JSONObject();
        params.put("userId", to);
        params.put("hello", from + " say:" + hello);
        params.put("type", 1);  // 1是通知消息，1是IM

        final Context context = this.getApplicationContext();
        HttpRequest request = new HttpRequest(HttpMethod.POST, allocServer + "/push");
        byte[] body = params.toString().getBytes(Constants.UTF_8);
        request.setBody(body, "application/json; charset=utf-8");
        request.setTimeout((int) TimeUnit.SECONDS.toMillis(10));
        request.setCallback(new HttpCallback() {
            @Override
            public void onResponse(final HttpResponse httpResponse) {
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (httpResponse.statusCode == 200) {
                            Toast.makeText(context, new String(httpResponse.body, Constants.UTF_8), Toast.LENGTH_SHORT).show();
                        } else {
                            Toast.makeText(context, httpResponse.reasonPhrase, Toast.LENGTH_SHORT).show();
                        }
                    }
                });
            }

            @Override
            public void onCancelled() {

            }
        });
        MPush.I.sendHttpProxy(request);
    }

    public void stopPush(View btn) {
        MPush.I.stopPush();
        Toast.makeText(this, "stop push", Toast.LENGTH_SHORT).show();
    }

    public void pausePush(View btn) {
        MPush.I.pausePush();
        Toast.makeText(this, "pause push", Toast.LENGTH_SHORT).show();
    }

    public void resumePush(View btn) {
        MPush.I.resumePush();
        Toast.makeText(this, "resume push", Toast.LENGTH_SHORT).show();
    }

    public void unbindUser(View btn) {
        MPush.I.unbindAccount();
        Toast.makeText(this, "unbind user", Toast.LENGTH_SHORT).show();
    }

    @Subscribe(threadMode = ThreadMode.MAIN)
    public void onMessageEvent(MessageEvent event) {
        String content = mLog.getText().toString();
        if(!TextUtils.isEmpty(event.getMsg())){
            mLog.setText(content + " \n\r接受到新的消息：" + event.getMsg());
        }

    };

    @Override
    public void onStart() {
        super.onStart();
        EventBus.getDefault().register(this);
    }

    @Override
    public void onStop() {
        super.onStop();
        EventBus.getDefault().unregister(this);
    }

}