#!/usr/bin/env python3
"""
给官方 sing-box-for-android (SFA) 源码打补丁：
连接到指定 Wi-Fi(SSID) -> 关闭 VPN；离开该 Wi-Fi -> 开启 VPN。

在 GitHub Actions 里运行，工作目录是 workflow 根目录，
官方源码在 ./sing-box-for-android，目标 Wi-Fi 名称来自环境变量 WIFI_SSID。

只做两件事：
  1. 新增一个 Kotlin 文件（监听服务 + 开机接收器 + 自启动 Provider）
  2. 在 AndroidManifest.xml 里补上权限和组件声明
不修改 SFA 的任何 Kotlin 源文件，这样官方代码更新时不容易冲突。
"""
import os
import pathlib
import sys

ROOT = pathlib.Path("sing-box-for-android")
MANIFEST = ROOT / "app/src/main/AndroidManifest.xml"
KT_DIR = ROOT / "app/src/main/java/io/nekohasekai/sfa/wifiswitch"

ssid = os.environ.get("WIFI_SSID", "").strip()
if not ssid:
    sys.exit("错误：仓库变量 WIFI_SSID 是空的。请先在 Settings -> Variables 里添加。")

if not MANIFEST.exists():
    sys.exit("错误：找不到 " + str(MANIFEST) + "，官方仓库目录结构可能变了。")


def kotlin_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("\n", " ")
        .replace("\r", " ")
    )


KOTLIN = r'''package io.nekohasekai.sfa.wifiswitch

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.database.Cursor
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.net.wifi.WifiInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.content.ContextCompat
import io.nekohasekai.sfa.bg.BoxService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * 常驻前台服务：监听 Wi-Fi。
 * 连上目标 Wi-Fi -> BoxService.stop()；离开目标 Wi-Fi -> BoxService.start()。
 */
class WifiAutoSwitchService : Service() {

    companion object {
        const val TARGET_SSID = "__WIFI_SSID__"
        private const val TAG = "WifiAutoSwitch"
        private const val CHANNEL_ID = "wifi_auto_switch"
        private const val NOTIFICATION_ID = 787801
        private const val DEBOUNCE_MS = 2500L

        fun ensureStarted(context: Context) {
            if (TARGET_SSID.isEmpty()) return
            try {
                ContextCompat.startForegroundService(
                    context,
                    Intent(context, WifiAutoSwitchService::class.java),
                )
            } catch (e: Throwable) {
                Log.w(TAG, "cannot start monitor service", e)
            }
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var pending: Job? = null
    private var targetNetwork: Network? = null
    private var callback: ConnectivityManager.NetworkCallback? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onCreate() {
        super.onCreate()
        goForeground()
        if (Build.VERSION.SDK_INT < 31) {
            stopSelf()
            return
        }
        register()
    }

    override fun onDestroy() {
        try {
            callback?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) }
        } catch (_: Throwable) {
        }
        callback = null
        scope.cancel()
        super.onDestroy()
    }

    private fun goForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Wi-Fi auto switch", NotificationManager.IMPORTANCE_MIN),
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Wi-Fi auto switch")
            .setContentText("Watching: $TARGET_SSID")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun register() {
        val cm = getSystemService(ConnectivityManager::class.java)
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .build()
        val cb = object : ConnectivityManager.NetworkCallback(
            ConnectivityManager.NetworkCallback.FLAG_INCLUDE_LOCATION_INFO,
        ) {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                val ssid = (caps.transportInfo as? WifiInfo)?.ssid?.removeSurrounding("\"")
                onWifi(network, ssid)
            }

            override fun onLost(network: Network) {
                onWifiLost(network)
            }
        }
        callback = cb
        cm.registerNetworkCallback(request, cb)
    }

    @Synchronized
    private fun onWifi(network: Network, ssid: String?) {
        val isTarget = ssid == TARGET_SSID
        if (isTarget && targetNetwork != network) {
            targetNetwork = network
            schedule("stop VPN (target Wi-Fi connected)") { BoxService.stop() }
        } else if (!isTarget && targetNetwork == network) {
            targetNetwork = null
            schedule("start VPN (left target Wi-Fi)") { BoxService.start() }
        }
    }

    @Synchronized
    private fun onWifiLost(network: Network) {
        if (targetNetwork == network) {
            targetNetwork = null
            schedule("start VPN (target Wi-Fi lost)") { BoxService.start() }
        }
    }

    private fun schedule(what: String, action: () -> Unit) {
        pending?.cancel()
        pending = scope.launch {
            delay(DEBOUNCE_MS)
            Log.i(TAG, what)
            try {
                action()
            } catch (e: Throwable) {
                Log.w(TAG, "failed: $what", e)
            }
        }
    }
}

/** 开机 / 应用更新后拉起监听服务。 */
class WifiAutoSwitchReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        WifiAutoSwitchService.ensureStarted(context)
    }
}

/** 应用进程一启动就拉起监听服务（不用改 SFA 的 Application 代码）。 */
class WifiAutoSwitchProvider : ContentProvider() {
    override fun onCreate(): Boolean {
        context?.let { WifiAutoSwitchService.ensureStarted(it) }
        return true
    }

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?,
    ): Cursor? = null

    override fun getType(uri: Uri): String? = null

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0

    override fun update(
        uri: Uri,
        values: ContentValues?,
        selection: String?,
        selectionArgs: Array<out String>?,
    ): Int = 0
}
'''

# ---------- 1. 写入 Kotlin 文件 ----------
KT_DIR.mkdir(parents=True, exist_ok=True)
(KT_DIR / "WifiAutoSwitch.kt").write_text(
    KOTLIN.replace("__WIFI_SSID__", kotlin_escape(ssid)), encoding="utf-8"
)
print("已写入", KT_DIR / "WifiAutoSwitch.kt")

# ---------- 2. 修改 AndroidManifest.xml ----------
text = MANIFEST.read_text(encoding="utf-8")

if "WifiAutoSwitchService" in text:
    print("Manifest 已经打过补丁，跳过")
else:
    wanted_permissions = [
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_BACKGROUND_LOCATION",
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.ACCESS_WIFI_STATE",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_SPECIAL_USE",
        "android.permission.RECEIVE_BOOT_COMPLETED",
    ]
    # 已存在的权限不能重复声明，否则 manifest 合并会报错
    perm_xml = ""
    for p in wanted_permissions:
        if 'android:name="' + p + '"' not in text:
            perm_xml += '    <uses-permission android:name="' + p + '" />\n'

    components = """
        <service
            android:name="io.nekohasekai.sfa.wifiswitch.WifiAutoSwitchService"
            android:exported="false"
            android:foregroundServiceType="specialUse">
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="wifi-vpn-switch" />
        </service>
        <receiver
            android:name="io.nekohasekai.sfa.wifiswitch.WifiAutoSwitchReceiver"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
                <action android:name="android.intent.action.MY_PACKAGE_REPLACED" />
            </intent-filter>
        </receiver>
        <provider
            android:name="io.nekohasekai.sfa.wifiswitch.WifiAutoSwitchProvider"
            android:authorities="${applicationId}.wifiswitch"
            android:exported="false" />
"""

    if "<application" not in text or "</application>" not in text:
        sys.exit("错误：Manifest 里找不到 <application> 标签，结构可能变了。")

    text = text.replace("<application", perm_xml + "    <application", 1)
    text = text.replace("</application>", components + "    </application>", 1)
    MANIFEST.write_text(text, encoding="utf-8")
    print("已修改", MANIFEST, "（新增权限：", perm_xml.count("<uses-permission"), "个）")

print("Wi-Fi 自动开关补丁完成，目标 Wi-Fi:", ssid)
