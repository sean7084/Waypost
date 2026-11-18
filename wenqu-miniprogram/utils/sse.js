// Server-sent events over wx.request for the native 问渠 mini program.
//
// Port of the grafted uni-app client's src/utils/api.js:764-844 (the
// enableChunked + onChunkReceived transport) and its frame parser, with the
// uni-app/fetch branches dropped: a native mini program has no fetch, no
// ReadableStream and no TextDecoder, so there is exactly one transport here.
//
// Frame format (wenqu/services/sse.py):
//     event: start|delta|final|done|error\ndata: {json}\n\n
//
// Chunking reality: WeChat delivers arbitrary byte slices. A frame can arrive
// split across several chunks (half-packet) or several frames can arrive in one
// chunk (sticky packet), and a multi-byte Chinese character can straddle a chunk
// boundary. So: incremental UTF-8 decode -> text buffer -> split on the blank
// line boundary -> parse each complete frame. Nothing is decoded per-chunk.
const { createUtf8Decoder, toUint8Array, decodeUtf8 } = require('./utf8');

const DEFAULT_TIMEOUT = 600000;
// A blank line ends a frame. SSE allows CRLF or LF line endings.
const BOUNDARY_RE = /\r\n\r\n|\n\n/;

function createStreamError(payload, fallbackDetail = '发送失败') {
  const detail =
    payload && typeof payload === 'object'
      ? payload.detail || payload.message || fallbackDetail
      : String(payload || fallbackDetail);
  const error = new Error(String(detail));
  error.payload = payload;
  return error;
}

function normalizeErrorPayload(payload) {
  if (payload && typeof payload === 'object') {
    if (!payload.detail && payload.errMsg) return { ...payload, detail: payload.errMsg };
    return payload;
  }
  return { detail: String(payload || '发送失败') };
}

// One `event:`/`data:` block -> {event, data}. Comment lines (`:`) and keep-alive
// blank lines are ignored; multi-line data is joined with \n per the SSE spec;
// a non-JSON payload degrades to {text: raw} instead of dropping the frame.
function parseSseBlock(block) {
  const text = String(block || '').trim();
  if (!text) return null;

  let eventName = 'message';
  const dataLines = [];

  text.split(/\r?\n/).forEach((line) => {
    if (!line || line.startsWith(':')) return;
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || 'message';
      return;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^\s/, ''));
    }
  });

  if (!dataLines.length) return null;
  const rawData = dataLines.join('\n').trim();
  if (!rawData) return null;
  if (rawData === '[DONE]') return { event: 'done', data: {} };

  try {
    return { event: eventName, data: JSON.parse(rawData) };
  } catch (_e) {
    return { event: eventName, data: { text: rawData } };
  }
}

// Incremental framer. push() accepts an ArrayBuffer / TypedArray / byte array;
// flush() handles a final frame that the server terminated without a blank line.
function createSseParser(onEvent) {
  const decoder = createUtf8Decoder();
  let buffer = '';

  const emit = (block) => {
    const parsed = parseSseBlock(block);
    if (parsed && typeof onEvent === 'function') onEvent(parsed.event, parsed.data);
    return Boolean(parsed);
  };

  return {
    push(chunk) {
      const bytes = toUint8Array(chunk);
      if (!bytes.length) return;
      buffer += decoder.push(bytes);

      let match = BOUNDARY_RE.exec(buffer);
      while (match) {
        const block = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        emit(block);
        match = BOUNDARY_RE.exec(buffer);
      }
    },

    flush() {
      buffer += decoder.flush();
      const trailing = buffer;
      buffer = '';
      emit(trailing);
    },

    get pendingLength() {
      return buffer.length + decoder.pendingLength;
    },
  };
}

// Whole-body fallback: the response arrived without chunking (older base
// library, or a proxy that buffered the stream). Split the completed text into
// frames, or synthesize a lifecycle from a plain JSON body.
function parseCompletedText(text, onEvent) {
  const normalized = String(text || '');
  let sawEvent = false;
  normalized
    .split(/\r?\n\r?\n/)
    .map((block) => block.trim())
    .filter(Boolean)
    .forEach((block) => {
      const parsed = parseSseBlock(block);
      if (!parsed) return;
      sawEvent = true;
      if (typeof onEvent === 'function') onEvent(parsed.event, parsed.data);
    });
  return sawEvent;
}

function synthesizeFromPayload(payload, onEvent) {
  const source = payload && typeof payload === 'object' ? payload : { reply: String(payload || '') };
  const sessionId = source.session_id || '';
  const meta = source.meta && typeof source.meta === 'object' ? source.meta : {};
  if (typeof onEvent !== 'function') return;
  onEvent('start', { session_id: sessionId, meta });
  onEvent('final', { ...source, session_id: sessionId, meta });
  onEvent('done', { session_id: sessionId });
}

function parseCompletedData(data, onEvent) {
  if (typeof data === 'string') {
    if (parseCompletedText(data, onEvent)) return true;
    const trimmed = data.trim();
    if (!trimmed) return false;
    try {
      synthesizeFromPayload(JSON.parse(trimmed), onEvent);
    } catch (_e) {
      synthesizeFromPayload({ reply: trimmed }, onEvent);
    }
    return true;
  }

  const bytes = toUint8Array(data);
  if (bytes.length) return parseCompletedData(decodeUtf8(bytes), onEvent);

  // A plain JSON body (no SSE framing). Binary shapes are excluded: an empty
  // ArrayBuffer/TypedArray is an empty stream, not a payload to synthesize from.
  const isBinary =
    data instanceof ArrayBuffer || (typeof ArrayBuffer.isView === 'function' && ArrayBuffer.isView(data));
  if (data && typeof data === 'object' && !isBinary) {
    synthesizeFromPayload(data, onEvent);
    return true;
  }
  return false;
}

// POST `url` with `data` as JSON and relay every SSE frame to onEvent(event, data).
//
// Resolves with {sawEvent, errorPayload} once the stream ends; rejects on a
// transport failure, a non-200 status, or an `error` frame. onEvent is called
// synchronously in frame order, and chunks are queued so a slow handler can
// never reorder frames.
function requestStream({ url, data, header = {}, timeout = DEFAULT_TIMEOUT, onEvent }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let sawEvent = false;
    let errorPayload = null;
    let queue = Promise.resolve();
    let task = null;

    const resolveOnce = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const rejectOnce = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };

    const handleEvent = (event, payload) => {
      sawEvent = true;
      if (event === 'error') errorPayload = normalizeErrorPayload(payload);
      if (typeof onEvent === 'function') onEvent(event, payload);
    };

    const parser = createSseParser(handleEvent);

    const finish = () => {
      if (errorPayload) {
        rejectOnce(createStreamError(errorPayload));
        return;
      }
      if (!sawEvent) {
        rejectOnce(createStreamError({ detail: 'Empty stream response' }));
        return;
      }
      resolveOnce({ sawEvent, errorPayload });
    };

    task = wx.request({
      url,
      method: 'POST',
      header,
      data,
      timeout,
      enableChunked: true,
      responseType: 'arraybuffer',
      success: (res) => {
        Promise.resolve()
          .then(async () => {
            if (settled) return;
            if (res.statusCode !== 200) {
              throw createStreamError(normalizeErrorPayload(res.data));
            }
            await queue;
            if (settled) return;
            parser.flush();
            if (sawEvent) {
              finish();
              return;
            }
            // No chunks were delivered: fall back to the completed body.
            if (!parseCompletedData(res.data, handleEvent)) {
              throw createStreamError({ detail: 'Empty stream response' });
            }
            finish();
          })
          .catch(rejectOnce);
      },
      fail: (err) => {
        rejectOnce(createStreamError(normalizeErrorPayload(err)));
      },
    });

    if (task && typeof task.onChunkReceived === 'function') {
      task.onChunkReceived((chunkResponse) => {
        queue = queue
          .then(() => {
            if (settled) return;
            parser.push(chunkResponse && chunkResponse.data);
          })
          .catch((error) => {
            try {
              if (task && typeof task.abort === 'function') task.abort();
            } catch (_e) {
              // abort is best-effort; the rejection below is what matters
            }
            rejectOnce(error);
          });
      });
    }
  });
}

module.exports = {
  DEFAULT_TIMEOUT,
  createStreamError,
  normalizeErrorPayload,
  parseSseBlock,
  createSseParser,
  parseCompletedText,
  parseCompletedData,
  requestStream,
};