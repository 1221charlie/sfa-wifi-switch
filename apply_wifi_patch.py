#!/usr/bin/env python3
"""
给官方 sing-box-for-android (SFA) 源码打补丁：
连接到指定 Wi-Fi(SSID) -> 关闭 VPN；离开该 Wi-Fi -> 开启 VPN。

Kotlin 代码放在同目录的 WifiAutoSwitch.kt.txt 里（模板），
本脚本负责：写入 Kotlin 文件 + 补全 AndroidManifest.xml。
目标 Wi-Fi 名称来自环境变量 WIFI_SSID。
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path("sing-box-for-android")
MANIFEST = ROOT / "app/src/main/AndroidManifest.xml"
KT_DIR = ROOT / "app/src/main/java/io/nekohasekai/sfa/wifiswitch"
TEMPLATE = HERE / "WifiAutoSwitch.kt.txt"

ssid = os.environ.get("WIFI_SSID", "").strip()
if not ssid:
    sys.exit("错误：仓库变量 WIFI_SSID 是空的。请先在 Settings -> Variables 里添加。")
if not TEMPLATE.exists():
    sys.exit("错误：找不到模板文件 " + str(TEMPLATE) + "（要和本脚本放在仓库同一层）。")
if not MANIFEST.exists():
    sys.exit("错误：找不到 " + str(MANIFEST) + "，官方仓库目录结构可能变了。")


def kotlin_escape(s):
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("\n", " ")
        .replace("\r", " ")
    )


# ---------- 1. 写入 Kotlin 文件 ----------
KT_DIR.mkdir(parents=True, exist_ok=True)
kt = TEMPLATE.read_text(encoding="utf-8").replace("__WIFI_SSID__", kotlin_escape(ssid))
(KT_DIR / "WifiAutoSwitch.kt").write_text(kt, encoding="utf-8")
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
