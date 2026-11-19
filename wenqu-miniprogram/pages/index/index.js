// 问渠 login page: wx.login -> /auth/wx_login -> chat.
//
// A first-ever login answers status:'new_user'; the customer is already issued a
// token pair, but we ask for profile authorization before entering the chat so
// the agent can address them by nickname. Phone authorization lives on the chat
// page (open-type="getPhoneNumber" needs a user gesture inside the conversation).
const api = require('../../utils/api');
const auth = require('../../utils/auth');

function wxLoginCode() {
  return new Promise((resolve, reject) => {
    wx.login({ success: resolve, fail: reject });
  });
}

function wxUserProfile(desc) {
  return new Promise((resolve, reject) => {
    wx.getUserProfile({ desc, success: resolve, fail: reject });
  });
}

Page({
  data: {
    loading: true,
    navigating: false,
    isNewUser: false,
    loginPrompt: '请登录以继续。',
    statusError: '',
  },

  onLoad(options) {
    if (options && options.loggedOut === '1') {
      this.setData({
        loading: false,
        navigating: false,
        isNewUser: false,
        loginPrompt: '已退出登录，请重新登录。',
        statusError: '',
      });
      return;
    }
    this.doLogin();
  },

  // Surface the backend's `detail` (string or DRF error list) instead of a
  // generic failure, so a disabled account or a bad code is actionable.
  resolveStatusError(error, fallback = '登录失败，请重试。') {
    const payload = error && typeof error === 'object' ? error.data : null;
    if (payload && Array.isArray(payload.detail) && payload.detail.length) {
      const first = payload.detail[0] || {};
      if (first.msg) return String(first.msg);
      if (first.message) return String(first.message);
    }
    if (payload && typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail.trim();
    if (payload && typeof payload.error === 'string' && payload.error.trim()) return payload.error.trim();
    if (payload && typeof payload.message === 'string' && payload.message.trim()) return payload.message.trim();
    if (error && typeof error.errMsg === 'string' && error.errMsg.trim()) return error.errMsg.trim();
    if (error && typeof error.message === 'string' && error.message.trim()) return error.message.trim();
    const statusCode = Number(error && error.statusCode);
    if (statusCode) return `请求失败 (${statusCode})`;
    return fallback;
  },

  async doLogin() {
    this.setData({ loading: true, navigating: false, isNewUser: false, statusError: '' });
    try {
      const loginRes = await wxLoginCode();
      if (!loginRes || !loginRes.code) throw new Error('微信登录未返回有效 code');

      const res = await api.wxLogin(loginRes.code);
      if (res && res.status === 'new_user') {
        auth.storeSession(res);
        this.syncAppSession();
        this.setData({ isNewUser: true, loading: false });
        return;
      }
      this.finishLogin(res);
    } catch (e) {
      console.error('[wenqu] login failed', e);
      this.setData({
        navigating: false,
        loading: false,
        loginPrompt: '登录失败，请重新登录。',
        statusError: this.resolveStatusError(e),
      });
    }
  },

  async handleAuthorize() {
    this.setData({ statusError: '' });
    try {
      const profileRes = await wxUserProfile('用于完善问渠会员资料');
      const res = await api.updateWxUser({ userInfo: profileRes.userInfo });
      this.finishLogin(res);
    } catch (e) {
      console.error('[wenqu] profile authorization failed', e);
      this.setData({ statusError: this.resolveStatusError(e, '授权失败，请重试。') });
    }
  },

  syncAppSession() {
    const app = getApp();
    if (app && typeof app.syncSession === 'function') app.syncSession();
  },

  finishLogin(data) {
    auth.storeSession(data);
    this.syncAppSession();
    this.setData({ loading: false, navigating: true });
    // redirectTo (not navigateTo): leaving the spent login page on the stack
    // would give the back button a dead end.
    wx.redirectTo({
      url: '/pages/chat/chat',
      fail: (err) => {
        console.error('[wenqu] navigate to chat failed', err);
        this.setData({ navigating: false, statusError: '进入聊天失败，请重试。' });
      },
    });
  },
});