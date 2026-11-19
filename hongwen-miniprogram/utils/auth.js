// Session storage for the 泓问 customer login.
//
// The token pair itself lives in utils/request.js (it owns the Bearer header and
// the 401 refresh path); this module owns the *profile* side of the session and
// the one-call clear used by logout and by a failed refresh.
const request = require('./request');

const USER_KEYS = ['user_id', 'external_id', 'external_user_id', 'nickname', 'avatar'];
const PHONE_KEY = 'has_phone';

// Persist a wx_login / update_wx_user payload:
// {status, auth_token, refresh, token_type, user:{user_id, external_id, nickname, avatar}}
function storeSession(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  request.setTokens(data.auth_token, data.refresh);

  const user = data.user && typeof data.user === 'object' ? data.user : null;
  if (!user) return;
  if (user.user_id) wx.setStorageSync('user_id', String(user.user_id));
  if (user.external_id) {
    // Both spellings: `external_id` is the API field, `external_user_id` is the
    // legacy storage key the chat page reads before sending a turn.
    wx.setStorageSync('external_id', String(user.external_id));
    wx.setStorageSync('external_user_id', String(user.external_id));
  }
  if (user.nickname) wx.setStorageSync('nickname', String(user.nickname));
  if (user.avatar) wx.setStorageSync('avatar', String(user.avatar));
}

function getStoredUser() {
  const user = {};
  USER_KEYS.forEach((key) => {
    const value = wx.getStorageSync(key);
    if (value) user[key] = value;
  });
  return user.user_id ? user : null;
}

function isLoggedIn() {
  return Boolean(request.getAccess());
}

function hasPhone() {
  return Boolean(wx.getStorageSync(PHONE_KEY));
}

function setHasPhone(value) {
  if (value) wx.setStorageSync(PHONE_KEY, true);
  else wx.removeStorageSync(PHONE_KEY);
}

function clearSession() {
  request.clearTokens();
  USER_KEYS.forEach((key) => wx.removeStorageSync(key));
  wx.removeStorageSync(PHONE_KEY);
}

module.exports = {
  USER_KEYS,
  PHONE_KEY,
  storeSession,
  getStoredUser,
  isLoggedIn,
  hasPhone,
  setHasPhone,
  clearSession,
};