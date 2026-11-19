import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const env = require('../config/env.js');
const request = require('./request.js');

// A wx stub that answers from a scripted queue and records every call, so the
// 401 -> refresh -> retry sequence can be asserted exactly.
function createWxMock({ requestResponses = [], uploadResponses = [], downloadResponses = [], storage = {} } = {}) {
  const store = new Map(Object.entries(storage));
  const calls = { request: [], uploadFile: [], downloadFile: [], reLaunch: [] };

  const respond = (queue, opts, api) => {
    calls[api].push(opts);
    const next = queue.shift();
    if (!next) throw new Error(`unexpected wx.${api}: ${opts.url}`);
    if (next.fail) {
      opts.fail(next.fail);
      return;
    }
    opts.success({ statusCode: next.statusCode, data: next.data, header: next.header || {} });
  };

  const wx = {
    env: { USER_DATA_PATH: 'wxfile://usr' },
    getStorageSync: (key) => (store.has(key) ? store.get(key) : ''),
    setStorageSync: (key, value) => store.set(key, value),
    removeStorageSync: (key) => store.delete(key),
    request: (opts) => respond(requestResponses, opts, 'request'),
    uploadFile: (opts) => respond(uploadResponses, opts, 'uploadFile'),
    downloadFile: (opts) => respond(downloadResponses, opts, 'downloadFile'),
    reLaunch: (opts) => {
      calls.reLaunch.push(opts);
      if (typeof opts.complete === 'function') opts.complete();
    },
  };

  return { wx, store, calls };
}

describe('config/env', () => {
  it('points API_BASE at the hongwen customer namespace', () => {
    expect(env.API_BASE.endsWith('/api/v1/hongwen')).toBe(true);
    expect(['dev', 'prod']).toContain(env.ENV);
  });

  it('derives the origin without the namespace path', () => {
    expect(env.API_ORIGIN).toBe(env.API_BASE.replace(/\/api\/v1\/hongwen$/, ''));
    expect(env.API_ORIGIN).not.toContain('/api/v1');
  });

  it('puts the refresh route outside the hongwen namespace', () => {
    expect(env.REFRESH_URL).toBe(`${env.API_ORIGIN}/api/v1/auth/token/refresh/`);
    expect(env.REFRESH_URL.startsWith(env.API_BASE)).toBe(false);
  });
});

describe('request headers and URLs', () => {
  let mock;
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
    mock = createWxMock({ storage: { auth_token: 'access-1', refresh_token: 'refresh-1' } });
    globalThis.wx = mock.wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  it('injects Bearer and the wechat-mini client type', async () => {
    mock = createWxMock({
      storage: { auth_token: 'access-1' },
      requestResponses: [{ statusCode: 200, data: { ok: true } }],
    });
    globalThis.wx = mock.wx;
    await request.request('/chatbot');
    const opts = mock.calls.request[0];
    expect(opts.url).toBe(`${env.API_BASE}/chatbot`);
    expect(opts.method).toBe('GET');
    expect(opts.header.Authorization).toBe('Bearer access-1');
    expect(opts.header['Helpdesk-Client-Type']).toBe('wechat-mini');
    expect(opts.header['Content-Type']).toBe('application/json');
  });

  it('omits Authorization when auth is false (wx_login)', async () => {
    mock = createWxMock({ requestResponses: [{ statusCode: 200, data: { status: 'ok' } }] });
    globalThis.wx = mock.wx;
    await request.request('/auth/wx_login', { method: 'POST', data: { code: 'c' }, auth: false });
    expect(mock.calls.request[0].header.Authorization).toBeUndefined();
  });

  it('passes an absolute URL through untouched', async () => {
    mock = createWxMock({ requestResponses: [{ statusCode: 200, data: {} }] });
    globalThis.wx = mock.wx;
    await request.request('https://elsewhere.example/x', { auth: false });
    expect(mock.calls.request[0].url).toBe('https://elsewhere.example/x');
  });

  it('flags a transport failure as networkError', async () => {
    mock = createWxMock({ requestResponses: [{ fail: { errMsg: 'request:fail' } }] });
    globalThis.wx = mock.wx;
    await expect(request.request('/chatbot')).rejects.toMatchObject({ message: 'NETWORK_ERROR', networkError: true });
  });

  it('throws HTTP_<status> with the payload for a non-2xx answer', async () => {
    mock = createWxMock({ requestResponses: [{ statusCode: 500, data: { detail: 'boom' } }] });
    globalThis.wx = mock.wx;
    await expect(request.request('/chatbot')).rejects.toMatchObject({
      message: 'HTTP_500',
      statusCode: 500,
      data: { detail: 'boom' },
    });
  });
});

describe('401 refresh-and-retry', () => {
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  it('refreshes once and retries with the new access token', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'old-access', refresh_token: 'refresh-1' },
      requestResponses: [
        { statusCode: 401, data: { detail: 'expired' } },
        { statusCode: 200, data: { access: 'new-access' } },
        { statusCode: 200, data: { sessions: [] } },
      ],
    });
    globalThis.wx = mock.wx;

    const result = await request.request('/chat/sessions');
    expect(result).toEqual({ sessions: [] });
    expect(mock.calls.request).toHaveLength(3);

    const refreshCall = mock.calls.request[1];
    expect(refreshCall.url).toBe(env.REFRESH_URL);
    expect(refreshCall.method).toBe('POST');
    expect(refreshCall.data).toEqual({ refresh: 'refresh-1' });
    // The refresh call itself must not carry the expired bearer token.
    expect(refreshCall.header.Authorization).toBeUndefined();

    expect(mock.calls.request[2].header.Authorization).toBe('Bearer new-access');
    expect(mock.store.get('auth_token')).toBe('new-access');
    // ROTATE_REFRESH_TOKENS is False: the stored refresh token is unchanged.
    expect(mock.store.get('refresh_token')).toBe('refresh-1');
    expect(mock.calls.reLaunch).toHaveLength(0);
  });

  it('does not refresh a second time when the retry also answers 401', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'old', refresh_token: 'refresh-1' },
      requestResponses: [
        { statusCode: 401, data: {} },
        { statusCode: 200, data: { access: 'new' } },
        { statusCode: 401, data: { detail: 'tv mismatch' } },
      ],
    });
    globalThis.wx = mock.wx;

    await expect(request.request('/chat/sessions')).rejects.toMatchObject({ message: 'HTTP_401', statusCode: 401 });
    expect(mock.calls.request).toHaveLength(3);
  });

  it('clears the session and routes to the login page when the refresh fails', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'old', refresh_token: 'bad-refresh' },
      requestResponses: [
        { statusCode: 401, data: {} },
        { statusCode: 401, data: { detail: 'Token is invalid' } },
      ],
    });
    globalThis.wx = mock.wx;

    await expect(request.request('/chat/sessions')).rejects.toMatchObject({ message: 'UNAUTHORIZED', statusCode: 401 });
    expect(mock.calls.request).toHaveLength(2);
    expect(mock.store.has('auth_token')).toBe(false);
    expect(mock.store.has('refresh_token')).toBe(false);
    expect(mock.calls.reLaunch).toHaveLength(1);
    expect(mock.calls.reLaunch[0].url).toBe('/pages/index/index?loggedOut=1');
  });

  it('does not attempt a refresh without a stored refresh token', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'old' },
      requestResponses: [{ statusCode: 401, data: {} }],
    });
    globalThis.wx = mock.wx;

    await expect(request.request('/chatbot')).rejects.toMatchObject({ message: 'UNAUTHORIZED' });
    expect(mock.calls.request).toHaveLength(1);
    expect(mock.calls.reLaunch).toHaveLength(1);
  });

  it('treats a refresh transport failure as a failed refresh', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'old', refresh_token: 'refresh-1' },
      requestResponses: [{ statusCode: 401, data: {} }, { fail: { errMsg: 'request:fail' } }],
    });
    globalThis.wx = mock.wx;

    await expect(request.request('/chatbot')).rejects.toMatchObject({ message: 'UNAUTHORIZED' });
    expect(mock.store.has('auth_token')).toBe(false);
  });

  it('refreshAccessToken reports success and failure', async () => {
    const ok = createWxMock({
      storage: { refresh_token: 'r' },
      requestResponses: [{ statusCode: 200, data: { access: 'a2' } }],
    });
    globalThis.wx = ok.wx;
    expect(await request.refreshAccessToken()).toBe(true);
    expect(ok.store.get('auth_token')).toBe('a2');

    const missing = createWxMock({ requestResponses: [] });
    globalThis.wx = missing.wx;
    expect(await request.refreshAccessToken()).toBe(false);
    expect(missing.calls.request).toHaveLength(0);
  });
});

describe('uploadFile', () => {
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  it('posts multipart with name=file, formData.type and the bearer header', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'access-1' },
      uploadResponses: [{ statusCode: 200, data: JSON.stringify({ file_id: 'f1', type: 'image', url: '/api/uploads/a.png' }) }],
    });
    globalThis.wx = mock.wx;

    const result = await request.uploadFile('/files/upload', 'wxfile://tmp/a.png', 'file', { type: 'image' });
    expect(result).toEqual({ file_id: 'f1', type: 'image', url: '/api/uploads/a.png' });
    const opts = mock.calls.uploadFile[0];
    expect(opts.url).toBe(`${env.API_BASE}/files/upload`);
    expect(opts.name).toBe('file');
    expect(opts.formData).toEqual({ type: 'image' });
    expect(opts.filePath).toBe('wxfile://tmp/a.png');
    expect(opts.header.Authorization).toBe('Bearer access-1');
    expect(opts.header['Helpdesk-Client-Type']).toBe('wechat-mini');
  });

  it('refreshes once and retries an upload that answered 401', async () => {
    const mock = createWxMock({
      storage: { auth_token: 'old', refresh_token: 'r' },
      requestResponses: [{ statusCode: 200, data: { access: 'new' } }],
      uploadResponses: [
        { statusCode: 401, data: JSON.stringify({ detail: 'expired' }) },
        { statusCode: 200, data: JSON.stringify({ file_id: 'f2' }) },
      ],
    });
    globalThis.wx = mock.wx;

    const result = await request.uploadFile('/files/upload', 'wxfile://tmp/a.png', 'file', { type: 'audio' });
    expect(result).toEqual({ file_id: 'f2' });
    expect(mock.calls.uploadFile).toHaveLength(2);
    expect(mock.calls.uploadFile[1].header.Authorization).toBe('Bearer new');
  });

  it('keeps a non-JSON body as raw text and still rejects on error status', async () => {
    const mock = createWxMock({ uploadResponses: [{ statusCode: 413, data: 'too large' }] });
    globalThis.wx = mock.wx;
    await expect(request.uploadFile('/files/upload', 'p', 'file', {})).rejects.toMatchObject({
      message: 'HTTP_413',
      statusCode: 413,
      data: 'too large',
    });
  });

  it('flags an upload transport failure as networkError', async () => {
    const mock = createWxMock({ uploadResponses: [{ fail: { errMsg: 'uploadFile:fail' } }] });
    globalThis.wx = mock.wx;
    await expect(request.uploadFile('/files/upload', 'p', 'file', {})).rejects.toMatchObject({ networkError: true });
  });
});

describe('asset URLs', () => {
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
    globalThis.wx = createWxMock({ storage: { auth_token: 'tok' } }).wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  it('joins root-canonical media paths to the ORIGIN, never to API_BASE', () => {
    expect(request.resolveAssetUrl('/api/uploads/hongwen/a.png')).toBe(`${env.API_ORIGIN}/api/uploads/hongwen/a.png`);
    expect(request.resolveAssetUrl('/api/provider/audio/abc')).toBe(`${env.API_ORIGIN}/api/provider/audio/abc`);
    expect(request.resolveAssetUrl('/api/uploads/a.png')).not.toContain('/api/v1/hongwen/api/uploads');
  });

  it('passes an absolute URL through and tolerates a missing leading slash', () => {
    expect(request.resolveAssetUrl('https://cdn.example/a.mp3')).toBe('https://cdn.example/a.mp3');
    expect(request.resolveAssetUrl('api/uploads/a.png')).toBe(`${env.API_ORIGIN}/api/uploads/a.png`);
    expect(request.resolveAssetUrl('')).toBe('');
    expect(request.resolveAssetUrl(null)).toBe('');
  });

  it('appends the access token for header-less media playback', () => {
    const url = request.buildAuthorizedAudioUrl('/api/provider/audio/abc');
    expect(url).toBe(`${env.API_ORIGIN}/api/provider/audio/abc?access_token=tok`);
  });

  it('is idempotent and preserves an existing query string', () => {
    const once = request.buildAuthorizedAudioUrl('/api/uploads/a.png?x=1');
    expect(once).toBe(`${env.API_ORIGIN}/api/uploads/a.png?x=1&access_token=tok`);
    expect(request.buildAuthorizedAudioUrl(once)).toBe(once);
  });

  it('returns the bare URL when no token is stored', () => {
    globalThis.wx = createWxMock().wx;
    expect(request.buildAuthorizedAudioUrl('/api/uploads/a.png')).toBe(`${env.API_ORIGIN}/api/uploads/a.png`);
  });

  it('appends query parameters with the right separator', () => {
    expect(request.appendQueryParam('https://x/a', 'k', 'v')).toBe('https://x/a?k=v');
    expect(request.appendQueryParam('https://x/a?b=1', 'k', 'v')).toBe('https://x/a?b=1&k=v');
    expect(request.appendQueryParam('https://x/a', 'k', '')).toBe('https://x/a');
  });
});

describe('token storage', () => {
  it('stores and clears the pair', () => {
    const mock = createWxMock();
    globalThis.wx = mock.wx;
    request.setTokens('a', 'r');
    expect(request.getAccess()).toBe('a');
    expect(request.getRefresh()).toBe('r');
    request.clearTokens();
    expect(request.getAccess()).toBe('');
    expect(request.getRefresh()).toBe('');
    globalThis.wx = undefined;
  });
});