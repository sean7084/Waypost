// Shared helpers for the 泓问 DevTools automator harness.
//
// Connects to the WeChat DevTools automation websocket (one connection at a
// time), redirects the client's API origin to the harness-owned Django server,
// and provides assertion / polling / screenshot / Django-fixture utilities.
//
// Why a harness-owned server on :8001? The primary Qwen/DashScope embedding
// endpoint is in arrears, so triage seeding/matching (item 5 quick_replies) only
// works with the Volcengine backup embed model. The user's :8000 dev server is
// left untouched; run-all starts (or reuses) a :8001 server with a process-local
// PRIMARY_EMBED_MODEL override and points the client at it via evaluate() - no
// repo file (config/env.js, .env) is modified. Tokens are shared (same SQLite DB
// and SECRET_KEY), so auth works identically on either port.
const automator = require('miniprogram-automator');
const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');

const WS_ENDPOINT = process.env.HARNESS_WS || 'ws://localhost:9420';
const PORT = String(process.env.HARNESS_PORT || '8001');
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const PYTHON = path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe');
const FIXTURES = path.join(__dirname, 'fixtures.py');
const SHOT_DIR = path.join(__dirname, 'screenshots');
const ASSET_DIR = path.join(__dirname, 'assets');
const EMBED_OVERRIDE = 'volcengine/doubao-embedding-vision-251215';
const ORIGIN = `http://127.0.0.1:${PORT}`;
const API_BASE = `${ORIGIN}/api/v1/hongwen`;
const REFRESH_URL = `${ORIGIN}/api/v1/auth/token/refresh/`;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERT FAILED: ${msg}`);
}

async function connect() {
  return automator.connect({ wsEndpoint: WS_ENDPOINT });
}

// Redirect the shared config/env module exports at runtime. request.js reads
// env.API_BASE / env.REFRESH_URL at call time, so every subsequent request (and
// the 401 refresh) hits the harness server. No source file is touched.
async function applyEnv(mini) {
  return mini.evaluate(
    (base, origin, refresh) => {
      const env = require('config/env');
      env.API_BASE = base;
      env.API_ORIGIN = origin;
      env.REFRESH_URL = refresh;
      return { API_BASE: env.API_BASE, REFRESH_URL: env.REFRESH_URL };
    },
    API_BASE,
    ORIGIN,
    REFRESH_URL
  );
}

async function shot(mini, name) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, `${name}.png`);
  await mini.screenshot({ path: file });
  return `screenshots/${name}.png`;
}

// Generic async poll. fn may return a promise; resolves with the first truthy
// value, rejects on timeout.
async function waitFor(fn, { timeout = 15000, interval = 200, label = 'condition' } = {}) {
  const start = Date.now();
  let lastErr;
  while (Date.now() - start < timeout) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (e) {
      lastErr = e;
    }
    await sleep(interval);
  }
  throw new Error(`TIMEOUT waiting for ${label}${lastErr ? ` (last error: ${lastErr.message})` : ''}`);
}

async function waitForData(page, predicate, { timeout = 15000, interval = 200, label = 'data', path: dataPath } = {}) {
  return waitFor(
    async () => {
      const data = await page.data(dataPath);
      return predicate(data) ? data : null;
    },
    { timeout, interval, label }
  );
}

async function clearStorage(mini) {
  return mini.evaluate(() => {
    wx.clearStorageSync();
    return true;
  });
}

// Restore the client's API origin to the repo default (:8000) after a harness
// run, so the DevTools simulator is left talking to the user's normal dev server.
async function restoreEnv(mini) {
  return mini.evaluate(() => {
    const env = require('config/env');
    env.API_BASE = 'http://127.0.0.1:8000/api/v1/hongwen';
    env.API_ORIGIN = 'http://127.0.0.1:8000';
    env.REFRESH_URL = 'http://127.0.0.1:8000/api/v1/auth/token/refresh/';
    return env.API_BASE;
  });
}

async function storageSnapshot(mini) {
  return mini.evaluate(() => ({
    auth_token: wx.getStorageSync('auth_token') || '',
    refresh_token: wx.getStorageSync('refresh_token') || '',
    user_id: wx.getStorageSync('user_id') || '',
    external_user_id: wx.getStorageSync('external_user_id') || '',
    nickname: wx.getStorageSync('nickname') || '',
    has_phone: wx.getStorageSync('has_phone') || false,
  }));
}

// Run a Django fixture command; returns the parsed JSON it prints.
function fixture(cmd, ...args) {
  const res = spawnSync(PYTHON, [FIXTURES, cmd, ...args], {
    cwd: REPO_ROOT,
    env: { ...process.env, PRIMARY_EMBED_MODEL: EMBED_OVERRIDE, PYTHONIOENCODING: 'utf-8' },
    encoding: 'utf-8',
    timeout: 180000,
  });
  if (res.error) throw new Error(`fixture ${cmd} spawn error: ${res.error.message}`);
  const lines = String(res.stdout || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const jsonLine = [...lines].reverse().find((l) => l.startsWith('{'));
  if (!jsonLine) {
    throw new Error(`fixture ${cmd} produced no JSON (status ${res.status}): ${res.stderr || res.stdout}`);
  }
  const parsed = JSON.parse(jsonLine);
  if (parsed.ok === false) throw new Error(`fixture ${cmd} failed: ${jsonLine}`);
  return parsed;
}

async function currentPagePath(mini) {
  const page = await mini.currentPage();
  return page ? page.path : '';
}

// Mock getUserProfile so handleAuthorize can complete without the native popup.
// automator strips success/fail from the mock args and delivers the RETURN value
// through the real success callback, so the mock must return (not call success).
async function mockUserProfile(mini, nickName, avatarUrl) {
  await mini.mockWxMethod(
    'getUserProfile',
    `function(){ return { userInfo: { nickName: ${JSON.stringify(nickName)}, avatarUrl: ${JSON.stringify(avatarUrl)}, gender: 1, country: 'China', province: 'Shanghai', city: 'Shanghai' }, errMsg: 'getUserProfile:ok' }; }`
  );
}

// Page.callMethod RPC is broken on base library 3.18.0 ("this.X is not a
// function"), so we invoke page methods through evaluate + getCurrentPages.
// callPageMethod awaits the result; firePageMethod returns immediately (for
// streaming methods we poll page.data concurrently).
async function callPageMethod(mini, method, ...args) {
  return mini.evaluate(
    async (m, a) => {
      const pages = getCurrentPages();
      const page = pages[pages.length - 1];
      const r = await page[m](...a);
      return r === undefined ? null : r;
    },
    method,
    args
  );
}

async function firePageMethod(mini, method, ...args) {
  return mini.evaluate(
    (m, a) => {
      const pages = getCurrentPages();
      const page = pages[pages.length - 1];
      const r = page[m](...a);
      if (r && typeof r.catch === 'function') r.catch(() => {});
      return true;
    },
    method,
    args
  );
}

// Ensure a logged-in chat page with bootstrap complete. Reuses stored tokens when
// present; otherwise drives wx.login (old user) or the new-user authorize flow.
async function gotoChat(mini) {
  const snap = await storageSnapshot(mini);
  if (snap.auth_token && snap.external_user_id) {
    const page = await mini.reLaunch('/pages/chat/chat');
    await waitForData(page, (d) => Boolean(d.currentSessionId), { timeout: 20000, label: 'chat bootstrap' });
    return page;
  }
  await mini.reLaunch('/pages/index/index');
  // Old user auto-redirects; new user shows the authorize button.
  const chatPage = await waitFor(
    async () => {
      const cur = await mini.currentPage();
      if (cur && cur.path === 'pages/chat/chat') {
        await waitForData(cur, (d) => Boolean(d.currentSessionId), { timeout: 20000, label: 'chat bootstrap' });
        return cur;
      }
      if (cur && cur.path === 'pages/index/index') {
        const data = await cur.data();
        if (data.isNewUser) {
          await mockUserProfile(mini, '泓问自动化用户', 'https://waypost.istore-tech.cn/static/avatar.png');
          await callPageMethod(mini, 'handleAuthorize');
        }
      }
      return null;
    },
    { timeout: 30000, interval: 400, label: 'login -> chat' }
  );
  return chatPage;
}

// Bind the logged-in customer to the test customer so triage runs without the
// LLM company-identification preamble (not one of the 11 runtime checks).
async function linkCustomer(mini) {
  const snap = await storageSnapshot(mini);
  if (!snap.external_user_id) throw new Error('linkCustomer: not logged in');
  return fixture('link-customer', snap.external_user_id);
}

module.exports = {
  WS_ENDPOINT,
  PORT,
  ORIGIN,
  API_BASE,
  REFRESH_URL,
  REPO_ROOT,
  PYTHON,
  SHOT_DIR,
  ASSET_DIR,
  EMBED_OVERRIDE,
  sleep,
  assert,
  connect,
  applyEnv,
  restoreEnv,
  shot,
  waitFor,
  waitForData,
  clearStorage,
  storageSnapshot,
  fixture,
  currentPagePath,
  mockUserProfile,
  callPageMethod,
  firePageMethod,
  gotoChat,
  linkCustomer,
};