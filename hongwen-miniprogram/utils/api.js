// Endpoint wrappers for the 泓问 customer API (PR4 contract).
//
// Every path is relative to config/env.js API_BASE = <origin>/api/v1/hongwen.
// Streaming turns go through utils/sse.js (wx.request enableChunked); everything
// else through utils/request.js (Bearer + single 401 refresh-and-retry).
//
// Video is deliberately absent: the backend answers 422 for type=video because
// WeChat's video moderation verdict arrives asynchronously, after the clip would
// already have reached the agent (api/hongwen_views.py CHAT_UPLOAD_UNSUPPORTED_TYPES).
const env = require('../config/env');
const request = require('./request');
const sse = require('./sse');

const UPLOAD_TYPES = ['image', 'audio'];

function call(handler, payload) {
  if (typeof handler === 'function') handler(payload);
}

function normalizeFinalPayload(payload, fallbackSessionId) {
  const source = payload && typeof payload === 'object' ? payload : { reply: String(payload || '') };
  const replyValue =
    source.reply != null ? source.reply : source.reply_text != null ? source.reply_text : source.text;
  return {
    ...source,
    reply: String(replyValue == null ? '' : replyValue),
    session_id: source.session_id || fallbackSessionId || '',
    meta: source.meta && typeof source.meta === 'object' ? source.meta : {},
  };
}

function extractReplyText(payload) {
  if (!payload || typeof payload !== 'object') return '';
  if (payload.reply != null) return String(payload.reply);
  if (payload.reply_text != null) return String(payload.reply_text);
  if (payload.text != null) return String(payload.text);
  return '';
}

// The SSE lifecycle: start{session_id,meta} -> delta{text}* ->
// final{reply,session_id,meta} -> done{session_id}, with error{detail,session_id}
// aborting the turn. Handlers are invoked synchronously in frame order.
async function streamTurn({ path, data, sessionId, onStart, onDelta, onFinal, onDone, onError }) {
  const state = {
    sessionId: sessionId || '',
    finalPayload: null,
    errorPayload: null,
    sawEvent: false,
    doneSeen: false,
  };

  const dispatch = (event, payload) => {
    state.sawEvent = true;
    const obj = payload && typeof payload === 'object' ? payload : { text: String(payload || '') };
    if (obj.session_id) state.sessionId = obj.session_id;

    if (event === 'start') {
      call(onStart, obj);
      return;
    }
    if (event === 'delta') {
      call(onDelta, obj);
      return;
    }
    if (event === 'final') {
      state.finalPayload = normalizeFinalPayload(obj, state.sessionId);
      state.sessionId = state.finalPayload.session_id || state.sessionId;
      call(onFinal, state.finalPayload);
      return;
    }
    if (event === 'done') {
      state.doneSeen = true;
      call(onDone, obj);
      return;
    }
    if (event === 'error') {
      state.errorPayload = sse.normalizeErrorPayload(obj);
      call(onError, state.errorPayload);
    }
  };

  try {
    await sse.requestStream({
      url: request.resolveUrl(path),
      data,
      header: request.jsonHeaders(),
      onEvent: dispatch,
    });
  } catch (err) {
    if (state.errorPayload) throw sse.createStreamError(state.errorPayload);
    throw err;
  }

  const finalPayload =
    state.finalPayload || normalizeFinalPayload({ reply: '', session_id: state.sessionId }, state.sessionId);
  if (!state.doneSeen) call(onDone, { session_id: finalPayload.session_id });
  return finalPayload;
}

// --- auth -------------------------------------------------------------------

function wxLogin(code) {
  return request.request('/auth/wx_login', {
    method: 'POST',
    data: { code },
    auth: false,
  });
}

function updateWxUser(payload) {
  return request.request('/auth/update_wx_user', { method: 'POST', data: payload });
}

function getPhone(payload) {
  return request.request('/auth/get-phone', { method: 'POST', data: payload });
}

// --- bootstrap / sessions ---------------------------------------------------

function createSession({ forceNew = false, sessionId = '' } = {}) {
  const query = [];
  if (forceNew) query.push('force_new=1');
  if (sessionId) query.push(`session_id=${encodeURIComponent(sessionId)}`);
  return request.request(`/chatbot${query.length ? `?${query.join('&')}` : ''}`, { method: 'GET' });
}

function listChatSessions() {
  return request.request('/chat/sessions', { method: 'GET' });
}

function sessionDetailPath(sessionId, currentSessionId) {
  const base = `/chat/sessions/${encodeURIComponent(sessionId || '')}`;
  return currentSessionId ? `${base}?current_session_id=${encodeURIComponent(currentSessionId)}` : base;
}

function renameChatSession(sessionId, title, { currentSessionId = '' } = {}) {
  return request.request(sessionDetailPath(sessionId, currentSessionId), {
    method: 'PATCH',
    data: { title: title == null ? null : String(title) },
  });
}

function deleteChatSession(sessionId, { currentSessionId = '' } = {}) {
  return request.request(sessionDetailPath(sessionId, currentSessionId), { method: 'DELETE' });
}

// --- chat -------------------------------------------------------------------

function sendTextOrForm({ type, content, sessionId }) {
  return request.request('/chat', {
    method: 'POST',
    data: { type, content, session_id: sessionId || null },
    timeout: sse.DEFAULT_TIMEOUT,
  });
}

function streamChatReply({ type, content, sessionId, onStart, onDelta, onFinal, onDone, onError }) {
  return streamTurn({
    path: '/chat/stream',
    data: { type, content, session_id: sessionId || null },
    sessionId,
    onStart,
    onDelta,
    onFinal,
    onDone,
    onError,
  });
}

// The backend only accepts a numeric message_id (a persisted Message PK). The
// client's optimistic ids for in-flight turns are `<ts>_<rand>`, so they are
// omitted and the server regenerates the latest assistant reply.
function regenerateBody(sessionId, messageId) {
  const data = { session_id: sessionId };
  const normalized = String(messageId || '').trim();
  if (/^\d+$/.test(normalized)) data.message_id = normalized;
  return data;
}

function regenerateReply({ sessionId, messageId }) {
  return request.request('/chat/regenerate', {
    method: 'POST',
    data: regenerateBody(sessionId, messageId),
    timeout: sse.DEFAULT_TIMEOUT,
  });
}

function streamRegenerateReply({ sessionId, messageId, onStart, onDelta, onFinal, onDone, onError }) {
  return streamTurn({
    path: '/chat/regenerate/stream',
    data: regenerateBody(sessionId, messageId),
    sessionId,
    onStart,
    onDelta,
    onFinal,
    onDone,
    onError,
  });
}

function sendMixedMessage({ text, imageFileIds, audioFileId, sessionId }) {
  const content = {};
  if (text && String(text).trim()) content.text = String(text).trim();
  if (imageFileIds && imageFileIds.length) content.image_file_ids = imageFileIds;
  if (audioFileId) content.audio_file_id = audioFileId;
  return sendTextOrForm({ type: 'mixed', content, sessionId });
}

// --- uploads / voice --------------------------------------------------------

function uploadFileOnly({ type, filePath }) {
  if (!UPLOAD_TYPES.includes(type)) {
    return Promise.reject(new Error(`不支持的上传类型: ${type}`));
  }
  return request
    .uploadFile('/files/upload', filePath, 'file', { type })
    .then((data) => {
      if (!data || !data.file_id) throw new Error('Upload succeeded without file_id');
      return data;
    });
}

function transcribeAudio({ filePath }) {
  return request.uploadFile('/transcribe', filePath, 'file', {});
}

function synthesizeSpeech({ text, messageId, languageType }) {
  return request
    .request('/speak', {
      method: 'POST',
      data: { text, message_id: messageId || '', language_type: languageType || undefined },
      timeout: 300000,
    })
    .then((payload) => {
      const source = payload && typeof payload === 'object' ? payload : {};
      return { ...source, audio_url: request.resolveAssetUrl(source.audio_url || '') };
    });
}

function synthesizeSpeechStream({ text, messageId, languageType }) {
  return request
    .request('/speak/stream', {
      method: 'POST',
      data: { text, message_id: messageId || '', language_type: languageType || undefined },
      timeout: 300000,
    })
    .then((payload) => {
      const source = payload && typeof payload === 'object' ? payload : {};
      return { ...source, stream_url: request.resolveAssetUrl(source.stream_url || '') };
    });
}

module.exports = {
  API_BASE: env.API_BASE,
  UPLOAD_TYPES,
  wxLogin,
  updateWxUser,
  getPhone,
  createSession,
  listChatSessions,
  renameChatSession,
  deleteChatSession,
  sendTextOrForm,
  sendMixedMessage,
  streamChatReply,
  regenerateReply,
  streamRegenerateReply,
  uploadFileOnly,
  transcribeAudio,
  synthesizeSpeech,
  synthesizeSpeechStream,
  normalizeFinalPayload,
  extractReplyText,
};