// JWT-aware network layer for the 问渠 customer API.
//
// Mirrors miniprogram/utils/request.js:74-79:
//   - injects `Authorization: Bearer <access>` on every authenticated call
//   - on 401, refreshes the access token ONCE and retries; if the refresh fails
//     it clears the session and routes back to pages/index
//   - network failures throw an error flagged `networkError`
//
// Wenqu specifics: the access token lives 2h and the refresh token 14d, and the
// refresh route is the shared Waypost SimpleJWT endpoint OUTSIDE the wenqu
// namespace (config/env.js REFRESH_URL). Every call also carries
// `Helpdesk-Client-Type: wechat-mini`, which the backend reads to pick the
// mini-program bootstrap payload.
const env = require('../config/env');

const TOKEN_KEY = 'auth_token';
const REFRESH_KEY = 'refresh_token';
const CLIENT_TYPE_HEADER = 'Helpdesk-Client-Type';
const CLIENT_TYPE = 'wechat-mini';
const LOGIN_PAGE = '/pages/index/index?loggedOut=1';

function getAccess() {
  return wx.getStorageSync(TOKEN_KEY) || '';
}

function getRefresh() {
  return wx.getStorageSync(REFRESH_KEY) || '';
}

function setTokens(access, refresh) {
  if (access) wx.setStorageSync(TOKEN_KEY, access);
  if (refresh) wx.setStorageSync(REFRESH_KEY, refresh);
}

function clearTokens() {
  wx.removeStorageSync(TOKEN_KEY);
  wx.removeStorageSync(REFRESH_KEY);
}

// Bearer + client-type headers. The legacy X-External-User-Id header is not sent:
// the customer is identified by the JWT, and the backend defaults the client type.
function authHeader() {
  const headers = { [CLIENT_TYPE_HEADER]: CLIENT_TYPE };
  const token = getAccess();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function jsonHeaders(extra) {
  return { 'Content-Type': 'application/json', ...authHeader(), ...(extra || {}) };
}

function rawRequest(options) {
  return new Promise((resolve, reject) => {
    wx.request({ ...options, success: resolve, fail: reject });
  });
}

// Guard against a burst of 401s each calling reLaunch (bootstrap + sessions +
// avatar can fail together). Reset once the login page is on top again.
let redirectingToLogin = false;

function redirectToLogin() {
  if (redirectingToLogin) return;
  try {
    const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : [];
    const top = pages && pages.length ? pages[pages.length - 1] : null;
    if (top && String(top.route || '') === 'pages/index/index') return;
  } catch (_e) {
    // getCurrentPages is unavailable in some test/runtime contexts; fall through.
  }
  redirectingToLogin = true;
  try {
    wx.reLaunch({
      url: LOGIN_PAGE,
      complete: () => {
        redirectingToLogin = false;
      },
    });
  } catch (_e) {
    redirectingToLogin = false;
  }
}

async function refreshAccessToken() {
  const refresh = getRefresh();
  if (!refresh) return false;
  try {
    const res = await rawRequest({
      url: env.REFRESH_URL,
      method: 'POST',
      data: { refresh },
      header: { 'Content-Type': 'application/json', [CLIENT_TYPE_HEADER]: CLIENT_TYPE },
    });
    if (res.statusCode === 200 && res.data && res.data.access) {
      // ROTATE_REFRESH_TOKENS is False: only `access` comes back.
      setTokens(res.data.access, res.data.refresh);
      return true;
    }
  } catch (_e) {
    // fall through to failure
  }
  return false;
}

function resolveUrl(path) {
  const value = String(path || '');
  return /^https?:\/\//i.test(value) ? value : `${env.API_BASE}${value}`;
}

async function request(path, options = {}) {
  const {
    method = 'GET',
    data = {},
    header = {},
    auth = true,
    retry = true,
    timeout = 60000,
  } = options;
  const url = resolveUrl(path);
  const headers = auth ? jsonHeaders(header) : { 'Content-Type': 'application/json', ...header };

  let res;
  try {
    res = await rawRequest({ url, method, data, header: headers, timeout });
  } catch (err) {
    throw Object.assign(new Error('NETWORK_ERROR'), { networkError: true, cause: err });
  }

  if (res.statusCode === 401 && auth && retry) {
    const ok = await refreshAccessToken();
    if (ok) return request(path, { method, data, header, auth, timeout, retry: false });
    clearTokens();
    redirectToLogin();
    throw Object.assign(new Error('UNAUTHORIZED'), { statusCode: 401, data: res.data });
  }
  if (res.statusCode >= 200 && res.statusCode < 300) return res.data;
  throw Object.assign(new Error(`HTTP_${res.statusCode}`), { statusCode: res.statusCode, data: res.data });
}

// Multipart upload. wx.uploadFile answers with a JSON *string* body, so it is
// parsed here; a 401 gets the same single refresh-and-retry as request().
function uploadFile(path, filePath, name, formData = {}, options = {}) {
  const { auth = true, retry = true, timeout = 300000 } = options;

  const attempt = () => new Promise((resolve, reject) => {
    const header = auth ? authHeader() : {};
    wx.uploadFile({
      url: resolveUrl(path),
      filePath,
      name,
      formData,
      header,
      timeout,
      success: (res) => {
        let data = res.data;
        try {
          data = JSON.parse(res.data);
        } catch (_e) {
          // keep raw text
        }
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(data);
        else reject(Object.assign(new Error(`HTTP_${res.statusCode}`), { statusCode: res.statusCode, data }));
      },
      fail: (err) => reject(Object.assign(new Error('NETWORK_ERROR'), { networkError: true, cause: err })),
    });
  });

  return attempt().catch(async (err) => {
    if (err && err.statusCode === 401 && auth && retry) {
      const ok = await refreshAccessToken();
      if (ok) return uploadFile(path, filePath, name, formData, { auth, timeout, retry: false });
      clearTokens();
      redirectToLogin();
    }
    throw err;
  });
}

// Binary download (TTS mp3). wx.downloadFile cannot stream, so the caller gets a
// temp file path and persists it via utils/audio.js.
function downloadFile(url, options = {}) {
  const { header = {}, timeout = 300000 } = options;
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      header: { ...authHeader(), ...header },
      timeout,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res);
        else reject(Object.assign(new Error(`HTTP_${res.statusCode}`), { statusCode: res.statusCode, data: res }));
      },
      fail: (err) => reject(Object.assign(new Error('NETWORK_ERROR'), { networkError: true, cause: err })),
    });
  });
}

// --- asset URLs -------------------------------------------------------------
//
// The server returns root-canonical media paths (`/api/uploads/<path>`,
// `/api/provider/audio/<id>`) that are mounted OUTSIDE /api/v1/wenqu. Use the
// returned URL as-is and join the ORIGIN only - never API_BASE, which would
// double the namespace prefix.
function resolveAssetUrl(value) {
  const normalized = String(value || '').trim();
  if (!normalized) return '';
  if (/^https?:\/\//i.test(normalized)) return normalized;
  return `${env.API_ORIGIN}/${normalized.replace(/^\/+/, '')}`;
}

function appendQueryParam(url, key, value) {
  const normalizedUrl = String(url || '').trim();
  const normalizedKey = String(key || '').trim();
  const normalizedValue = String(value || '').trim();
  if (!normalizedUrl || !normalizedKey || !normalizedValue) return normalizedUrl;
  const separator = normalizedUrl.includes('?') ? '&' : '?';
  return `${normalizedUrl}${separator}${encodeURIComponent(normalizedKey)}=${encodeURIComponent(normalizedValue)}`;
}

// WeChat's <image> and InnerAudioContext accept a URL only - no headers - so
// protected media carries the access token as a query parameter
// (WenquQueryTokenAuthentication on the server). Idempotent: a URL that already
// carries access_token is returned unchanged.
function buildAuthorizedAudioUrl(value) {
  const resolved = resolveAssetUrl(value);
  if (!resolved) return '';
  if (/[?&]access_token=/.test(resolved)) return resolved;
  const token = getAccess();
  if (!token) return resolved;
  return appendQueryParam(resolved, 'access_token', token);
}

module.exports = {
  TOKEN_KEY,
  REFRESH_KEY,
  CLIENT_TYPE_HEADER,
  CLIENT_TYPE,
  LOGIN_PAGE,
  request,
  uploadFile,
  downloadFile,
  getAccess,
  getRefresh,
  setTokens,
  clearTokens,
  authHeader,
  jsonHeaders,
  refreshAccessToken,
  redirectToLogin,
  resolveUrl,
  resolveAssetUrl,
  appendQueryParam,
  buildAuthorizedAudioUrl,
};