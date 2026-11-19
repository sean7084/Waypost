import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const sse = require('./sse.js');

const encode = (text) => new TextEncoder().encode(text);
const frame = (event, data) => encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

// Concatenate byte chunks the way wx.request hands them over.
function bytes(...parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  parts.forEach((part) => {
    out.set(part, offset);
    offset += part.length;
  });
  return out;
}

describe('parseSseBlock', () => {
  it('parses an event + JSON data frame', () => {
    expect(sse.parseSseBlock('event: delta\ndata: {"text":"你好"}')).toEqual({
      event: 'delta',
      data: { text: '你好' },
    });
  });

  it('defaults the event name to message', () => {
    expect(sse.parseSseBlock('data: {"a":1}')).toEqual({ event: 'message', data: { a: 1 } });
  });

  it('ignores comment / keep-alive lines', () => {
    expect(sse.parseSseBlock(': keep-alive\n\nevent: done\ndata: {"session_id":"s1"}')).toEqual({
      event: 'done',
      data: { session_id: 's1' },
    });
  });

  it('joins multi-line data with a newline', () => {
    expect(sse.parseSseBlock('event: final\ndata: {"reply":\ndata: "x"}')).toEqual({
      event: 'final',
      data: { reply: 'x' },
    });
  });

  it('maps the [DONE] sentinel to a done event', () => {
    expect(sse.parseSseBlock('data: [DONE]')).toEqual({ event: 'done', data: {} });
  });

  it('degrades a non-JSON payload to text instead of dropping it', () => {
    expect(sse.parseSseBlock('event: delta\ndata: plain text')).toEqual({
      event: 'delta',
      data: { text: 'plain text' },
    });
  });

  it('returns null for empty / data-less blocks', () => {
    expect(sse.parseSseBlock('')).toBeNull();
    expect(sse.parseSseBlock('   ')).toBeNull();
    expect(sse.parseSseBlock('event: delta')).toBeNull();
    expect(sse.parseSseBlock('data:   ')).toBeNull();
  });

  it('tolerates CRLF line endings', () => {
    expect(sse.parseSseBlock('event: start\r\ndata: {"session_id":"s"}')).toEqual({
      event: 'start',
      data: { session_id: 's' },
    });
  });
});

describe('createSseParser', () => {
  it('splits sticky packets (several frames in one chunk)', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    parser.push(bytes(frame('start', { session_id: 's' }), frame('delta', { text: 'a' }), frame('done', { session_id: 's' })));
    expect(seen).toEqual([
      ['start', { session_id: 's' }],
      ['delta', { text: 'a' }],
      ['done', { session_id: 's' }],
    ]);
  });

  it('reassembles half packets (one frame across many chunks)', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    const whole = frame('final', { reply: '重启路由器', session_id: 's', meta: { model_used: 'm' } });
    for (let i = 0; i < whole.length; i += 3) {
      parser.push(whole.slice(i, i + 3));
    }
    expect(seen).toEqual([['final', { reply: '重启路由器', session_id: 's', meta: { model_used: 'm' } }]]);
  });

  it('reassembles a multi-byte character split across a chunk boundary', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    const whole = frame('delta', { text: '泓问😀' });
    // 'event: delta\ndata: {"text":"' is 28 ASCII bytes, so byte 29 lands inside
    // 问 (3 bytes) and byte 35 lands inside the emoji (4 bytes).
    parser.push(whole.slice(0, 29));
    parser.push(whole.slice(29, 35));
    parser.push(whole.slice(35));
    expect(seen).toEqual([['delta', { text: '泓问😀' }]]);
  });

  it('handles CRLF frame boundaries', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    parser.push(encode('event: delta\r\ndata: {"text":"x"}\r\n\r\n'));
    expect(seen).toEqual([['delta', { text: 'x' }]]);
  });

  it('emits a trailing frame without a blank line on flush', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    parser.push(encode('event: done\ndata: {"session_id":"s"}'));
    expect(seen).toEqual([]);
    parser.flush();
    expect(seen).toEqual([['done', { session_id: 's' }]]);
  });

  it('ignores keep-alive comments between frames', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    parser.push(encode(': ping\n\n'));
    parser.push(frame('delta', { text: 'x' }));
    expect(seen).toEqual([['delta', { text: 'x' }]]);
  });

  it('ignores empty chunks', () => {
    const seen = [];
    const parser = sse.createSseParser((event, data) => seen.push([event, data]));
    parser.push(new Uint8Array(0));
    parser.push(null);
    parser.flush();
    expect(seen).toEqual([]);
  });
});

// --- transport --------------------------------------------------------------

function mockWx({ chunks = [], statusCode = 200, body = null, fail = null, chunked = true } = {}) {
  const state = { options: null, chunkHandler: null, aborted: false };
  globalThis.wx = {
    request(options) {
      state.options = options;
      const task = {
        onChunkReceived(handler) {
          state.chunkHandler = handler;
        },
        abort() {
          state.aborted = true;
        },
      };
      // Deliver asynchronously: requestStream attaches onChunkReceived after
      // wx.request returns, exactly like the real runtime.
      setTimeout(() => {
        if (fail) {
          options.fail(fail);
          return;
        }
        chunks.forEach((chunk) => {
          if (state.chunkHandler) state.chunkHandler({ data: chunk });
        });
        options.success({ statusCode, data: body });
      }, 0);
      return chunked ? task : {};
    },
  };
  return state;
}

describe('requestStream', () => {
  let originalWx;

  beforeEach(() => {
    originalWx = globalThis.wx;
  });

  afterEach(() => {
    globalThis.wx = originalWx;
  });

  it('POSTs with enableChunked and an arraybuffer response type', async () => {
    const state = mockWx({ chunks: [frame('done', { session_id: 's' })] });
    await sse.requestStream({ url: 'https://x/chat/stream', data: { type: 'text' }, header: { A: 'b' }, onEvent: () => {} });
    expect(state.options.method).toBe('POST');
    expect(state.options.enableChunked).toBe(true);
    expect(state.options.responseType).toBe('arraybuffer');
    expect(state.options.url).toBe('https://x/chat/stream');
    expect(state.options.header).toEqual({ A: 'b' });
    expect(state.options.data).toEqual({ type: 'text' });
    expect(typeof state.chunkHandler).toBe('function');
  });

  it('relays the full start/delta/final/done lifecycle in order', async () => {
    const chunks = [
      frame('start', { session_id: 's', meta: { used_knowledge_base: true } }),
      frame('delta', { text: '你' }),
      frame('delta', { text: '好' }),
      frame('final', { reply: '你好', session_id: 's', meta: { quick_replies: [{ label_zh: '重启', value: 'reboot' }] } }),
      frame('done', { session_id: 's' }),
    ];
    // One chunk per frame, plus a sticky pair to exercise both paths.
    mockWx({ chunks: [bytes(chunks[0], chunks[1]), chunks[2], bytes(chunks[3], chunks[4])] });
    const seen = [];
    const result = await sse.requestStream({
      url: 'https://x/chat/stream',
      data: {},
      onEvent: (event, data) => seen.push([event, data]),
    });
    expect(seen.map((entry) => entry[0])).toEqual(['start', 'delta', 'delta', 'final', 'done']);
    expect(seen[2][1]).toEqual({ text: '好' });
    expect(seen[3][1].meta.quick_replies).toEqual([{ label_zh: '重启', value: 'reboot' }]);
    expect(result.sawEvent).toBe(true);
  });

  it('rejects on a non-200 status with the server detail', async () => {
    mockWx({ statusCode: 400, body: { detail: '内容违规' } });
    await expect(
      sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: () => {} })
    ).rejects.toThrow('内容违规');
  });

  it('relays and then rejects on an error frame', async () => {
    mockWx({ chunks: [frame('start', { session_id: 's' }), frame('error', { detail: '模型超时', session_id: 's' })] });
    const seen = [];
    await expect(
      sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: (event) => seen.push(event) })
    ).rejects.toThrow('模型超时');
    expect(seen).toEqual(['start', 'error']);
  });

  it('rejects on a transport failure', async () => {
    mockWx({ fail: { errMsg: 'request:fail timeout' } });
    await expect(
      sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: () => {} })
    ).rejects.toThrow('request:fail timeout');
  });

  it('rejects on an empty stream', async () => {
    mockWx({ chunks: [], body: new Uint8Array(0) });
    await expect(
      sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: () => {} })
    ).rejects.toThrow('Empty stream response');
  });

  it('falls back to the completed arraybuffer body when no chunks arrive', async () => {
    const whole = bytes(frame('start', { session_id: 's' }), frame('final', { reply: 'ok', session_id: 's' }), frame('done', { session_id: 's' }));
    mockWx({ chunks: [], body: whole.buffer });
    const seen = [];
    await sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: (event) => seen.push(event) });
    expect(seen).toEqual(['start', 'final', 'done']);
  });

  it('falls back to a completed text body', async () => {
    mockWx({ chunks: [], body: 'event: delta\ndata: {"text":"x"}\n\nevent: done\ndata: {}\n\n' });
    const seen = [];
    await sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: (event) => seen.push(event) });
    expect(seen).toEqual(['delta', 'done']);
  });

  it('synthesizes a lifecycle from a plain JSON body', async () => {
    mockWx({ chunked: false, body: { reply: 'ok', session_id: 's', meta: { a: 1 } } });
    const seen = [];
    await sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: (event, data) => seen.push([event, data]) });
    expect(seen.map((entry) => entry[0])).toEqual(['start', 'final', 'done']);
    expect(seen[1][1]).toEqual({ reply: 'ok', session_id: 's', meta: { a: 1 } });
  });

  it('works when the runtime returns a task without onChunkReceived', async () => {
    mockWx({ chunked: false, body: bytes(frame('done', { session_id: 's' })).buffer });
    const seen = [];
    await sse.requestStream({ url: 'https://x/chat/stream', data: {}, onEvent: (event) => seen.push(event) });
    expect(seen).toEqual(['done']);
  });
});

describe('parseCompletedText', () => {
  it('reports whether any SSE frame was found', () => {
    const seen = [];
    expect(sse.parseCompletedText('event: done\ndata: {}\n\n', (event) => seen.push(event))).toBe(true);
    expect(seen).toEqual(['done']);
    expect(sse.parseCompletedText('not sse at all', () => {})).toBe(false);
    expect(sse.parseCompletedText('', () => {})).toBe(false);
  });
});