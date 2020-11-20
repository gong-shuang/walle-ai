package com.huawei.gspush;

import androidx.appcompat.app.AppCompatActivity;

import android.Manifest;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.Toast;

import com.mpush.demo.MainActivity;
import com.walle.push.sdkHuawei.HuaweiActivity;
import com.walle.pushxiaomi.XiaomiActivity;

import pub.devrel.easypermissions.AfterPermissionGranted;
import pub.devrel.easypermissions.EasyPermissions;

public class HomeActivity extends AppCompatActivity {

    private static final String[] ALL_PERMISSION =
            {
                    //电话通讯录
                    android.Manifest.permission.GET_ACCOUNTS,
                    android.Manifest.permission.READ_PHONE_STATE,
                    //位置
                    android.Manifest.permission.ACCESS_FINE_LOCATION,
                    android.Manifest.permission.ACCESS_COARSE_LOCATION,
                    android.Manifest.permission.ACCESS_FINE_LOCATION,
                    //相机、麦克风
                    android.Manifest.permission.RECORD_AUDIO,
                    android.Manifest.permission.WAKE_LOCK,
                    android.Manifest.permission.CAMERA,
                    //存储空间
                    android.Manifest.permission.WRITE_EXTERNAL_STORAGE,
                    android.Manifest.permission.WRITE_SETTINGS,
                    //网络
                    android.Manifest.permission.INTERNET,
                    android.Manifest.permission.ACCESS_NETWORK_STATE,
                    Manifest.permission.ACCESS_WIFI_STATE
            };
    private static final int PERMISSION_CODE = 222;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_home);

        //获取权限
        acquirePermissions();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);

        // 将结果转发给 EasyPermissions
        EasyPermissions.onRequestPermissionsResult(requestCode, permissions, grantResults, this);
    }

    @AfterPermissionGranted(PERMISSION_CODE)
    public void acquirePermissions() {
        if (hasLocationAndContactsPermissions()) {
            // Have permissions, do the thing!
            Toast.makeText(this, "TODO: Location and Contacts things", Toast.LENGTH_LONG).show();
        } else {
            // Ask for both permissions
            EasyPermissions.requestPermissions(
                    this,
                    getString(R.string.all_permissions),
                    PERMISSION_CODE,
                    ALL_PERMISSION);
        }
    }

    private boolean hasLocationAndContactsPermissions() {
        return EasyPermissions.hasPermissions(this, ALL_PERMISSION);
    }

    public void hello(View btn) {
        Intent intent = new Intent(HomeActivity.this, MainActivity.class);
        startActivity(intent);
    }


    public void gotoHuawei(View btn) {
        Intent intent = new Intent(HomeActivity.this, HuaweiActivity.class);
        startActivity(intent);
    }

    public void xiaomi(View btn) {
        Intent intent = new Intent(HomeActivity.this, XiaomiActivity.class);
        startActivity(intent);
    }
}