# ============================================================
# R8 / ProGuard 规则
# 说明：release 已开启 minifyEnabled + shrinkResources，
#       这里的 keep 项都是「运行时靠反射 / ServiceLoader / JNI 找类」的地方，
#       删掉任何一条都可能导致 release 包崩溃，改动需真机回归。
# ============================================================

# ------------------------------------------------------------
# Eclipse Paho MQTT
# ------------------------------------------------------------
# Paho 通过 META-INF/services 里的 NetworkModuleFactory 做 SPI 发现
# （ServiceLoader 按类名字符串加载实现类），因此整个包必须保留类名，
# 否则混淆后 ServiceLoader 找不到实现，表现为连不上 broker。
-keep class org.eclipse.paho.client.mqttv3.** { *; }
-keepnames class org.eclipse.paho.client.mqttv3.** { *; }
-keep class * implements org.eclipse.paho.client.mqttv3.spi.NetworkModuleFactory { *; }
-dontwarn org.eclipse.paho.client.mqttv3.**

# ------------------------------------------------------------
# WebRTC（org.webrtc）
# ------------------------------------------------------------
# AAR 自带 proguard.txt 已 -keep class org.webrtc.**，这里显式再声明一份，
# 防止依赖升级后 consumer 规则丢失。JNI 回调依赖方法签名，不能混淆。
-keep class org.webrtc.** { *; }
-keepclasseswithmembernames class org.webrtc.** { native <methods>; }
-dontwarn org.webrtc.**

# ------------------------------------------------------------
# 通用
# ------------------------------------------------------------
# 注解、泛型签名、内部类信息：反射 / 回调恢复时可能用到
-keepattributes Signature,InnerClasses,EnclosingMethod,*Annotation*

# 枚举的 values() / valueOf() 被系统或反射调用时依赖名字
-keepclassmembers enum * {
    public static **[] values();
    public static ** valueOf(java.lang.String);
}

# 实现 Parcelable 的类（当前未使用，留作扩展）
-keepclassmembers class * implements android.os.Parcelable {
    public static final ** CREATOR;
}
