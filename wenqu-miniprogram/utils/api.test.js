import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const env = require('../config/env.js');
const api = require('./api.js');

const encode = (text) => new TextEncoder().encode(text);
const frame = (event, data) => encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

function createWxMock({ requestResponses = [], uploadResponses = [], chunks = [], storage = { auth_token: 'tok', external_user_id: 'wx_1' } } = {}) {
  const store = new Map(Object.entries(storage));
  const calls = { request: [], uploadFile: [] };

  const wx = {
    env: { USER_DATA_PATH: 'wxfile://usr' },
    getStorageSync: (key) => (store.has(key) ? store.get(key) : ''),
    setStorageSync: (key, value) => store.set(key, value),
    removeStorageSync: (key) => store.delete(key),
    request(opts) {
      calls.request.push(opts);
      if (opts.enableChunked) {
        const task = {
          onChunkReceived(handler) {
            setTimeout(() => {
              chunks.forEach((chunk) => handler({ data: chunk }));
              opts.success({ statusCode: 200, data: new ArrayBuffer(0) });
            }, 0);
          },
          abort() {},
        };
        return task;
      }
      const next = requestResponses.shift();
      if (!next) throw new Error(`unexpected wx.request: ${opts.url}`);
      if (next.fail) opts.fail(next.fail);
      else opts.success({ statusCode: next.statusCode, data: next.data });
      return {};
    },
    uploadFile(opts) {
      calls.uploadFile.push(opts);
      const next = uploadResponses.shift();
      if (!next) throw new Error(`unexpected wx.uploadFile: ${opts.url}`);
      opts.success({ statusCode: next.statusCode, data: next.data });
    },
    reLaunch(opts) {
      if (typeof opts.complete === 'function') opts.complete();
    },
  };

  return { wx, store, calls };
}

describe('endpoint contract', () => {
  let mock;
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  const use = (options) => {
    mock = createWxMock(options);
    globalThis.wx = mock.wx;
    return mock;
  };

  it('wx_login POSTs the code without a bearer token', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: { status: 'ok', auth_token: 'a', refresh: 'r' } }] });
    await api.wxLogin('the-code');
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/auth/wx_login`);
    expect(m.calls.request[0].method).toBe('POST');
    expect(m.calls.request[0].data).toEqual({ code: 'the-code' });
    expect(m.calls.request[0].header.Authorization).toBeUndefined();
  });

  it('update_wx_user and get-phone are authenticated POSTs', async () => {
    const m = use({
      requestResponses: [
        { statusCode: 200, data: { status: 'ok' } },
        { statusCode: 200, data: { status: 'success', phone: '13800000000' } },
      ],
    });
    await api.updateWxUser({ userInfo: { nickName: 'n', avatarUrl: 'a' } });
    await api.getPhone({ code: 'phone-code' });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/auth/update_wx_user`);
    expect(m.calls.request[1].url).toBe(`${env.API_BASE}/auth/get-phone`);
    expect(m.calls.request[1].data).toEqual({ code: 'phone-code' });
    expect(m.calls.request[1].header.Authorization).toBe('Bearer tok');
  });

  it('bootstrap encodes force_new and session_id', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: { session_id: 's', greeting: 'hi' } }] });
    await api.createSession({ forceNew: true, sessionId: 's 1' });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/chatbot?force_new=1&session_id=s%201`);
    expect(m.calls.request[0].method).toBe('GET');
  });

  it('bootstrap without options hits the bare chatbot path', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: {} }] });
    await api.createSession();
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/chatbot`);
  });

  it('lists, renames and deletes sessions with current_session_id', async () => {
    const m = use({
      requestResponses: [
        { statusCode: 200, data: { sessions: [] } },
        { statusCode: 200, data: { sessions: [] } },
        { statusCode: 200, data: { current_session_deleted: true } },
      ],
    });
    await api.listChatSessions();
    await api.renameChatSession('s1', '新名字', { currentSessionId: 'cur' });
    await api.deleteChatSession('s1', { currentSessionId: 'cur' });

    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/chat/sessions`);
    expect(m.calls.request[1].url).toBe(`${env.API_BASE}/chat/sessions/s1?current_session_id=cur`);
    expect(m.calls.request[1].method).toBe('PATCH');
    expect(m.calls.request[1].data).toEqual({ title: '新名字' });
    expect(m.calls.request[2].method).toBe('DELETE');
  });

  it('a null title clears the custom session name', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: {} }] });
    await api.renameChatSession('s1', null);
    expect(m.calls.request[0].data).toEqual({ title: null });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/chat/sessions/s1`);
  });

  it('POSTs /chat with type, content and session_id', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: { reply: 'ok' } }] });
    await api.sendTextOrForm({ type: 'text', content: { text: 'hi' }, sessionId: 's' });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/chat`);
    expect(m.calls.request[0].data).toEqual({ type: 'text', content: { text: 'hi' }, session_id: 's' });
  });

  it('sends a null session_id when there is no session yet', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: {} }] });
    await api.sendTextOrForm({ type: 'text', content: {}, sessionId: '' });
    expect(m.calls.request[0].data.session_id).toBeNull();
  });

  it('mixed messages carry image/audio ids and never video ids', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: {} }] });
    await api.sendMixedMessage({ text: ' hi ', imageFileIds: ['i1'], audioFileId: 'a1', sessionId: 's' });
    const body = m.calls.request[0].data;
    expect(body.type).toBe('mixed');
    expect(body.content).toEqual({ text: 'hi', image_file_ids: ['i1'], audio_file_id: 'a1' });
    expect(body.content.video_file_ids).toBeUndefined();
  });

  it('uploads images and audio to /files/upload as multipart name=file', async () => {
    const m = use({
      uploadResponses: [
        { statusCode: 200, data: JSON.stringify({ file_id: 'f1', type: 'image', url: '/api/uploads/a.png' }) },
        { statusCode: 200, data: JSON.stringify({ file_id: 'f2', type: 'audio' }) },
      ],
    });
    await api.uploadFileOnly({ type: 'image', filePath: 'wxfile://tmp/a.png' });
    await api.uploadFileOnly({ type: 'audio', filePath: 'wxfile://tmp/a.mp3' });
    expect(m.calls.uploadFile[0].url).toBe(`${env.API_BASE}/files/upload`);
    expect(m.calls.uploadFile[0].name).toBe('file');
    expect(m.calls.uploadFile[0].formData).toEqual({ type: 'image' });
    expect(m.calls.uploadFile[1].formData).toEqual({ type: 'audio' });
  });

  it('refuses a video upload before any network call', async () => {
    const m = use({ uploadResponses: [] });
    await expect(api.uploadFileOnly({ type: 'video', filePath: 'wxfile://tmp/a.mp4' })).rejects.toThrow('不支持的上传类型');
    expect(m.calls.uploadFile).toHaveLength(0);
    expect(api.UPLOAD_TYPES).toEqual(['image', 'audio']);
  });

  it('rejects an upload answer without file_id', async () => {
    use({ uploadResponses: [{ statusCode: 200, data: JSON.stringify({ type: 'image' }) }] });
    await expect(api.uploadFileOnly({ type: 'image', filePath: 'p' })).rejects.toThrow('file_id');
  });

  it('transcribes through /transcribe', async () => {
    const m = use({ uploadResponses: [{ statusCode: 200, data: JSON.stringify({ text: '你好', model: 'asr' }) }] });
    const result = await api.transcribeAudio({ filePath: 'wxfile://tmp/a.mp3' });
    expect(m.calls.uploadFile[0].url).toBe(`${env.API_BASE}/transcribe`);
    expect(result.text).toBe('你好');
  });

  it('resolves speak audio_url against the origin, not the namespace', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: { audio_url: '/api/provider/audio/x', model: 'tts' } }] });
    const result = await api.synthesizeSpeech({ text: 'hi', messageId: '12' });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/speak`);
    expect(m.calls.request[0].data).toEqual({ text: 'hi', message_id: '12', language_type: undefined });
    expect(result.audio_url).toBe(`${env.API_ORIGIN}/api/provider/audio/x`);
  });

  it('resolves speak/stream stream_url against the origin', async () => {
    const m = use({ requestResponses: [{ statusCode: 200, data: { stream_url: '/api/v1/wenqu/speak/stream/abc' } }] });
    const result = await api.synthesizeSpeechStream({ text: 'hi', messageId: '12' });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/speak/stream`);
    expect(result.stream_url).toBe(`${env.API_ORIGIN}/api/v1/wenqu/speak/stream/abc`);
  });

  it('only sends a numeric message_id to regenerate', async () => {
    const m = use({
      requestResponses: [
        { statusCode: 200, data: { reply: 'a' } },
        { statusCode: 200, data: { reply: 'b' } },
      ],
    });
    await api.regenerateReply({ sessionId: 's', messageId: '42' });
    await api.regenerateReply({ sessionId: 's', messageId: '1730000000_ab12' });
    expect(m.calls.request[0].url).toBe(`${env.API_BASE}/chat/regenerate`);
    expect(m.calls.request[0].data).toEqual({ session_id: 's', message_id: '42' });
    expect(m.calls.request[1].data).toEqual({ session_id: 's' });
  });
});

describe('streaming lifecycle', () => {
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  it('relays start/delta/final/done and returns the normalized final payload', async () => {
    const chunks = [
      frame('start', { session_id: 's1', meta: { used_knowledge_base: true } }),
      frame('delta', { text: '重启' }),
      frame('delta', { text: '路由器' }),
      frame('final', {
        reply: '重启路由器',
        session_id: 's1',
        meta: { quick_replies: [{ label_zh: '已解决', value: 'resolved' }], model_used: 'm' },
      }),
      frame('done', { session_id: 's1' }),
    ];
    const mock = createWxMock({ chunks });
    globalThis.wx = mock.wx;

    const seen = { start: [], delta: [], final: [], done: [], error: [] };
    const result = await api.streamChatReply({
      type: 'text',
      content: { text: 'hi' },
      sessionId: 's0',
      onStart: (p) => seen.start.push(p),
      onDelta: (p) => seen.delta.push(p),
      onFinal: (p) => seen.final.push(p),
      onDone: (p) => seen.done.push(p),
      onError: (p) => seen.error.push(p),
    });

    expect(mock.calls.request[0].url).toBe(`${env.API_BASE}/chat/stream`);
    expect(mock.calls.request[0].data).toEqual({ type: 'text', content: { text: 'hi' }, session_id: 's0' });
    expect(mock.calls.request[0].header.Authorization).toBe('Bearer tok');
    expect(seen.start).toHaveLength(1);
    expect(seen.delta.map((p) => p.text)).toEqual(['重启', '路由器']);
    expect(seen.done).toEqual([{ session_id: 's1' }]);
    expect(seen.error).toEqual([]);
    expect(result.reply).toBe('重启路由器');
    expect(result.session_id).toBe('s1');
    expect(result.meta.quick_replies).toEqual([{ label_zh: '已解决', value: 'resolved' }]);
  });

  it('synthesizes a done callback when the server omits it', async () => {
    const mock = createWxMock({
      chunks: [frame('start', { session_id: 's' }), frame('final', { reply: 'ok', session_id: 's' })],
    });
    globalThis.wx = mock.wx;
    const done = [];
    const result = await api.streamChatReply({ type: 'text', content: {}, sessionId: 's', onDone: (p) => done.push(p) });
    expect(done).toEqual([{ session_id: 's' }]);
    expect(result.reply).toBe('ok');
  });

  it('rejects and reports an error frame', async () => {
    const mock = createWxMock({
      chunks: [frame('start', { session_id: 's' }), frame('error', { detail: '模型超时', session_id: 's' })],
    });
    globalThis.wx = mock.wx;
    const errors = [];
    await expect(
      api.streamChatReply({ type: 'text', content: {}, sessionId: 's', onError: (p) => errors.push(p) })
    ).rejects.toThrow('模型超时');
    expect(errors).toEqual([{ detail: '模型超时', session_id: 's' }]);
  });

  it('streams regenerate to /chat/regenerate/stream', async () => {
    const mock = createWxMock({
      chunks: [
        frame('start', { session_id: 's', meta: { regenerated: true } }),
        frame('final', { reply: 'new', session_id: 's', meta: { regenerated: true } }),
        frame('done', { session_id: 's' }),
      ],
    });
    globalThis.wx = mock.wx;
    const result = await api.streamRegenerateReply({ sessionId: 's', messageId: '7' });
    expect(mock.calls.request[0].url).toBe(`${env.API_BASE}/chat/regenerate/stream`);
    expect(mock.calls.request[0].data).toEqual({ session_id: 's', message_id: '7' });
    expect(result.meta.regenerated).toBe(true);
  });

  it('normalizes reply_text / text aliases in a final payload', () => {
    expect(api.normalizeFinalPayload({ reply_text: 'a' }, 's').reply).toBe('a');
    expect(api.normalizeFinalPayload({ text: 'b' }, 's').reply).toBe('b');
    expect(api.normalizeFinalPayload({}, 's')).toEqual({ reply: '', session_id: 's', meta: {} });
    expect(api.extractReplyText({ reply: 'x' })).toBe('x');
    expect(api.extractReplyText(null)).toBe('');
  });
});