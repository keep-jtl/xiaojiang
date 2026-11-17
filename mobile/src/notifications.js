// Android 系统通知（@capacitor/local-notifications）。
// Web/浏览器环境没有原生桥接时返回 unsupported，绝不假装系统通知已弹出。
import { LocalNotifications } from '@capacitor/local-notifications';

let available = true;
try {
  // 插件在 Web 端可能没有实现；import 本身成功，但调用会抛错，统一在调用处兜底。
  if (!LocalNotifications || typeof LocalNotifications.checkPermissions !== 'function') available = false;
} catch (_) {
  available = false;
}

function channel() {
  return {
    id: 'xltc_alerts',
    name: '小财通盯盘提醒',
    description: '指数/基金条件触发的本地提醒',
    importance: 5,
    visibility: 1,
    sound: 'default',
    vibration: true
  };
}

export async function notificationStatus() {
  if (!available) return { supported: false, granted: false, text: '当前环境不支持原生通知' };
  try {
    const r = await LocalNotifications.checkPermissions();
    return {
      supported: true,
      granted: r.display === 'granted',
      state: r.display,
      text: r.display === 'granted' ? 'Android 系统通知已开启' : '尚未获得通知权限'
    };
  } catch (err) {
    return { supported: false, granted: false, text: '通知插件不可用：' + (err?.message || '未知错误') };
  }
}

export async function requestNotificationPermission() {
  if (!available) return { supported: false, granted: false, text: '当前环境不支持原生通知' };
  try {
    const r = await LocalNotifications.requestPermissions();
    const ok = r.display === 'granted';
    return { supported: true, granted: ok, state: r.display, text: ok ? '通知权限已开启' : '通知权限未授予，可到系统设置中打开' };
  } catch (err) {
    return { supported: false, granted: false, text: '通知权限申请失败：' + (err?.message || '未知错误') };
  }
}

export async function showLocalNotification(title, body) {
  if (!available) return { ok: false, error: '当前环境不支持原生通知' };
  try {
    const st = await notificationStatus();
    if (!st.granted) {
      await requestNotificationPermission();
      const again = await notificationStatus();
      if (!again.granted) return { ok: false, error: '通知权限未开启' };
    }
    try {
      await LocalNotifications.createChannel(channel());
    } catch (_) { /* 渠道已存在时忽略 */ }
    const id = (Date.now() % 2147483647);
    await LocalNotifications.schedule({
      notifications: [{
        id,
        title,
        body,
        channelId: 'xltc_alerts',
        schedule: { at: new Date(Date.now() + 1000) },
        iconColor: '#B7791F'
      }]
    });
    return { ok: true, id };
  } catch (err) {
    return { ok: false, error: err?.message || '发送本地通知失败' };
  }
}
