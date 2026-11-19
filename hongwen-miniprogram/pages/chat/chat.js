// 泓问 chat page - native rewrite of the grafted uni-app src/pages/chat/chat.vue.
//
// Responsibilities: bootstrap the conversation, drive the session drawer
// (new / rename / delete), stream the assistant reply with a typewriter effect,
// and own the voice-note and TTS playback lifecycles. Rendering lives in
// components/message-list and components/input-bar.
//
// Native differences from the Vue original:
//   * no computed properties - derived UI state is pushed through _syncMessageUi
//   * streaming deltas use indexed setData paths (`messages[3].content`) instead
//     of re-sending the whole array on every token
//   * the scroll-view lives inside message-list so scroll-into-view ids never
//     have to cross a custom-component boundary
const api = require('../../utils/api');
const auth = require('../../utils/auth');
const request = require('../../utils/request');
const audio = require('../../utils/audio');
const headerMetrics = require('../../utils/header-metrics');

// With navigationStyle:custom the page starts at y=0, so the header must be
// inset by the status bar itself. The uni-app original subtracted 48px to cancel
// an inflated `screenTop` on its test device; natively that would pull the title
// under the status bar (or go negative), so the inset is used as measured and
// clamped at 0. The +4 keeps the title vertically centred against the capsule.
const HEADER_TOP_INSET_EXTRA_PX = 0;
const HEADER_HEIGHT_EXTRA_PX = 4;

function nextId() {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function toast(title, icon = 'none') {
  wx.showToast({ title: String(title || '').slice(0, 20), icon });
}

function showConfirmModal(options) {
  return new Promise((resolve, reject) => {
    wx.showModal({ ...options, success: resolve, fail: reject });
  });
}

Page({
  data: {
    messages: [],
    headerTopInset: 0,
    headerContentHeight: 44,
    menuPanelTop: 56,
    menuOpen: false,
    sessionSummaries: [],
    sessionsLoading: false,
    sessionsError: '',
    sessionActionBusyId: '',
    sending: false,
    scrollIntoView: '',
    currentSessionId: '',
    pendingMediaFileId: '',
    pendingMediaPreviewPath: '',
    pendingMediaType: '',
    hasPhone: false,
    latestAssistantId: '',
    showLatestActions: false,
    assistantActionState: { activeMessageId: '', loadingMessageId: '' },
  },

  onLoad() {
    this.speechAudioContext = null;
    this.speechUrlMap = {};
    this.applyHeaderMetrics();
    this.setData({ hasPhone: auth.hasPhone() });
    this.startNewSession();
  },

  onUnload() {
    this.setData({ menuOpen: false });
    this.destroySpeechContext();
  },

  // --- header layout --------------------------------------------------------

  applyHeaderMetrics() {
    const metrics = headerMetrics.resolveChatHeaderMetrics({
      ...headerMetrics.readWindowMetrics(),
      headerTopInsetExtraPx: HEADER_TOP_INSET_EXTRA_PX,
      headerHeightExtraPx: HEADER_HEIGHT_EXTRA_PX,
    });
    this.setData({
      headerTopInset: Math.max(0, metrics.headerTopInset),
      headerContentHeight: metrics.headerContentHeight,
      menuPanelTop: Math.max(0, metrics.headerTopInset) + metrics.headerContentHeight + 8,
    });
  },

  // --- session drawer -------------------------------------------------------

  toggleMenu() {
    const nextState = !this.data.menuOpen;
    this.setData({ menuOpen: nextState });
    if (nextState) this.refreshSessionSummaries({ silent: true });
  },

  closeMenu() {
    this.setData({ menuOpen: false });
  },

  noop() {},

  handleLogout() {
    this.closeMenu();
    wx.showModal({
      title: '退出登录',
      content: '确认退出当前账号？',
      success: ({ confirm }) => {
        if (confirm) this.performLogout();
      },
    });
  },

  performLogout() {
    this.destroySpeechContext();
    this.speechUrlMap = {};
    auth.clearSession();
    const app = getApp();
    if (app && typeof app.syncSession === 'function') app.syncSession();
    this.setData({
      menuOpen: false,
      messages: [],
      scrollIntoView: '',
      currentSessionId: '',
      hasPhone: false,
      sessionSummaries: [],
      sessionsError: '',
      sessionActionBusyId: '',
      pendingMediaFileId: '',
      pendingMediaPreviewPath: '',
      pendingMediaType: '',
      latestAssistantId: '',
      showLatestActions: false,
      assistantActionState: { activeMessageId: '', loadingMessageId: '' },
    });
    wx.reLaunch({ url: '/pages/index/index?loggedOut=1' });
  },

  async onGetPhoneNumber(e) {
    if (!e.detail || e.detail.errMsg !== 'getPhoneNumber:ok') {
      toast('授权失败');
      return;
    }
    try {
      await api.getPhone({ code: e.detail.code });
      auth.setHasPhone(true);
      this.setData({ hasPhone: true });
      const app = getApp();
      if (app && typeof app.syncSession === 'function') app.syncSession();
      toast('已取得手机号', 'success');
    } catch (_err) {
      toast('获取手机号失败');
    }
  },

  setSessionSummaries(sessions) {
    const list = Array.isArray(sessions)
      ? sessions
        .map((session) => ({
          session_id: String((session && session.session_id) || ''),
          title: String((session && session.title) || '').trim(),
          preview: String((session && session.preview) || '').trim(),
          updated_at: String((session && session.updated_at) || '').trim(),
          created_at: String((session && session.created_at) || '').trim(),
          timeLabel: this.formatSessionTime(session && session.updated_at),
        }))
        .filter((session) => session.session_id)
      : [];
    this.setData({ sessionSummaries: list });
  },

  formatSessionTime(value) {
    const normalized = String(value || '').trim();
    if (!normalized) return '';
    // iOS rejects 'YYYY-MM-DD HH:MM:SS'; normalize the separator first.
    const date = new Date(normalized.replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return normalized.slice(0, 10);

    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const sameDay =
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate();
    if (sameDay) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    if (date.getFullYear() === now.getFullYear()) return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  },

  async refreshSessionSummaries({ silent = false } = {}) {
    if (this.data.sessionsLoading) return;
    this.setData({ sessionsLoading: true });
    if (!silent) this.setData({ sessionsError: '' });
    try {
      const result = await api.listChatSessions();
      this.setSessionSummaries(result && result.sessions);
      this.setData({ sessionsError: '' });
    } catch (_error) {
      this.setData({ sessionsError: '历史对话加载失败' });
      if (!silent) toast('历史对话加载失败');
    } finally {
      this.setData({ sessionsLoading: false });
    }
  },

  normalizeSessionTitle(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  },

  applySessionBootstrap(payload) {
    const data = payload && typeof payload === 'object' ? payload : {};
    this.setData({ currentSessionId: data.session_id ? String(data.session_id) : '' });
    if (data.external_user_id) wx.setStorageSync('external_user_id', String(data.external_user_id));
    if (Array.isArray(data.sessions)) this.setSessionSummaries(data.sessions);

    const restored = this.restoreHistoryMessages(data.messages);
    if (!restored) {
      this.setData({ messages: [] });
      if (data.greeting) {
        this.appendMessage({ id: 'greeting', role: 'assistant', type: 'text', content: data.greeting });
      }
    }
    this.resetSessionUiState();
  },

  resetSessionUiState() {
    this.speechUrlMap = {};
    this.destroySpeechContext();
    this.setData({
      pendingMediaFileId: '',
      pendingMediaPreviewPath: '',
      pendingMediaType: '',
      assistantActionState: { activeMessageId: '', loadingMessageId: '' },
    });
  },

  async startNewSession({ forceNew = false, sessionId = '', closeMenu = false } = {}) {
    try {
      const payload = await api.createSession({ forceNew, sessionId });
      this.applySessionBootstrap(payload);
      this.setData({ sessionsError: '' });
      if (closeMenu) this.closeMenu();
    } catch (_e) {
      toast(sessionId ? '切换会话失败' : '创建会话失败');
    }
  },

  async handleCreateNewSession() {
    if (this.data.sending) return;
    await this.startNewSession({ forceNew: true, closeMenu: true });
  },

  async handleSelectSession(e) {
    const sessionId = String((e.currentTarget.dataset.sessionId || '')).trim();
    if (!sessionId || this.data.sending || this.data.sessionActionBusyId) return;
    if (sessionId === String(this.data.currentSessionId || '')) {
      this.closeMenu();
      return;
    }
    await this.startNewSession({ sessionId, closeMenu: true });
  },

  handleSessionActions(e) {
    const sessionId = String((e.currentTarget.dataset.sessionId || '')).trim();
    if (!sessionId || this.data.sending || this.data.sessionActionBusyId) return;
    const session = this.data.sessionSummaries.find((item) => item.session_id === sessionId);
    wx.showActionSheet({
      itemList: ['重命名', '删除'],
      success: ({ tapIndex }) => {
        if (tapIndex === 0) this.renameSession(session);
        else if (tapIndex === 1) this.deleteSession(session);
      },
    });
  },

  async renameSession(session) {
    const sessionId = String((session && session.session_id) || '').trim();
    if (!sessionId || this.data.sending || this.data.sessionActionBusyId) return;

    let modalResult;
    try {
      modalResult = await showConfirmModal({
        title: '重命名会话',
        editable: true,
        placeholderText: '输入新的会话名称',
        content: String((session && session.title) || ''),
      });
    } catch (_error) {
      toast('无法打开重命名弹窗');
      return;
    }
    if (!modalResult || !modalResult.confirm) return;

    const title = this.normalizeSessionTitle(modalResult.content);
    this.setData({ sessionActionBusyId: sessionId });
    try {
      const result = await api.renameChatSession(sessionId, title, {
        currentSessionId: this.data.currentSessionId,
      });
      if (result && Array.isArray(result.sessions)) this.setSessionSummaries(result.sessions);
      toast(title ? '会话已重命名' : '已恢复默认标题');
    } catch (_error) {
      toast('重命名会话失败');
    } finally {
      this.setData({ sessionActionBusyId: '' });
    }
  },

  async deleteSession(session) {
    const sessionId = String((session && session.session_id) || '').trim();
    if (!sessionId || this.data.sending || this.data.sessionActionBusyId) return;

    let modalResult;
    try {
      modalResult = await showConfirmModal({ title: '删除会话', content: '确认删除这个会话吗？' });
    } catch (_error) {
      toast('无法打开删除确认');
      return;
    }
    if (!modalResult || !modalResult.confirm) return;

    this.setData({ sessionActionBusyId: sessionId });
    try {
      const result = await api.deleteChatSession(sessionId, { currentSessionId: this.data.currentSessionId });
      if (result && result.current_session_deleted) {
        if (result.bootstrap) {
          this.applySessionBootstrap(result.bootstrap);
          this.closeMenu();
        } else {
          await this.startNewSession({ forceNew: true, closeMenu: true });
        }
      } else if (result && Array.isArray(result.sessions)) {
        this.setSessionSummaries(result.sessions);
      }
      toast('会话已删除');
    } catch (_error) {
      toast('删除会话失败');
    } finally {
      this.setData({ sessionActionBusyId: '' });
    }
  },

  // --- message list state ---------------------------------------------------

  messageIndex(messageId) {
    const normalized = String(messageId || '');
    return this.data.messages.findIndex((msg) => String(msg.id || '') === normalized);
  },

  getMessageById(messageId) {
    const index = this.messageIndex(messageId);
    return index === -1 ? null : this.data.messages[index];
  },

  getMessageContent(messageId) {
    const message = this.getMessageById(messageId);
    if (!message) return '';
    return typeof message.content === 'string' ? message.content : '';
  },

  syncMessageUi() {
    let latestAssistantId = '';
    for (let index = this.data.messages.length - 1; index >= 0; index -= 1) {
      const message = this.data.messages[index];
      if (message && message.role === 'assistant' && message.type === 'text') {
        latestAssistantId = String(message.id || '');
        break;
      }
    }
    this.setData({
      latestAssistantId,
      showLatestActions: Boolean(latestAssistantId) && !this.data.sending,
    });
  },

  appendMessage(msg) {
    const messages = this.data.messages.concat([msg]);
    this.setData({ messages, scrollIntoView: `m_${messages.length - 1}` });
    this.syncMessageUi();
  },

  removeMessage(messageId) {
    const index = this.messageIndex(messageId);
    if (index === -1) return;
    const messages = this.data.messages.slice();
    messages.splice(index, 1);
    this.setData({
      messages,
      scrollIntoView: messages.length ? `m_${messages.length - 1}` : '',
    });
    this.syncMessageUi();
  },

  setMessageContent(messageId, content) {
    const index = this.messageIndex(messageId);
    if (index === -1) return;
    this.setData({ [`messages[${index}].content`]: content, scrollIntoView: `m_${index}` });
  },

  setMessageMeta(messageId, meta) {
    const index = this.messageIndex(messageId);
    if (index === -1) return;
    this.setData({
      [`messages[${index}].attachments`]: this.extractTriageAttachments(meta),
      [`messages[${index}].quickReplies`]: this.extractQuickReplies(meta),
      scrollIntoView: `m_${index}`,
    });
    this.syncMessageUi();
  },

  setAudioTranscript(messageId, transcription) {
    const index = this.messageIndex(messageId);
    if (index === -1) return;
    const current = this.data.messages[index];
    const content = current && typeof current.content === 'object' && current.content ? current.content : {};
    this.setData({
      [`messages[${index}].content`]: { ...content, transcription },
      scrollIntoView: `m_${index}`,
    });
  },

  normalizeHistoryAssetUrl(value) {
    const normalized = String(value || '').trim();
    if (!normalized) return '';
    return request.buildAuthorizedAudioUrl(normalized) || normalized;
  },

  extractTriageAttachments(meta) {
    const source = meta && Array.isArray(meta.attachments) ? meta.attachments : [];
    const seen = {};
    const result = [];
    source.forEach((item) => {
      if (!item || item.type !== 'image') return;
      const rawUrl = String(item.url || item.path || '').trim();
      if (!rawUrl || seen[rawUrl]) return;
      seen[rawUrl] = true;
      result.push({ url: this.normalizeHistoryAssetUrl(rawUrl), caption: String(item.caption || '').trim() });
    });
    return result;
  },

  extractQuickReplies(meta) {
    const source = meta && Array.isArray(meta.quick_replies) ? meta.quick_replies : [];
    return source
      .map((item) => ({
        label_zh: String((item && item.label_zh) || (item && item.value) || '').trim(),
        value: String((item && item.value) || '').trim(),
      }))
      .filter((item) => item.value);
  },

  // The server already expands a mixed turn into separate user rows whose ids
  // carry :image:N / :audio / :text suffixes; audio content is
  // {playable, filePath, duration, transcription}.
  normalizeHistoryMessage(message, index) {
    if (!message || typeof message !== 'object') return null;
    const role = message.role === 'user' ? 'user' : 'assistant';
    const type = String(message.type || 'text');
    const id = String(message.id || `${role}_${index}`);
    const content = message.content && typeof message.content === 'object' ? message.content : {};

    if (type === 'form') {
      return {
        id,
        role,
        type: 'form',
        content: { category: String(content.category || ''), detail: String(content.detail || '') },
      };
    }

    if (type === 'image') {
      const filePath = this.normalizeHistoryAssetUrl(content.filePath || content.previewPath || content.url);
      if (!filePath) return { id, role, type: 'text', content: '图片消息' };
      return { id, role, type: 'image', content: { ...content, filePath, previewPath: filePath } };
    }

    if (type === 'audio') {
      const filePath = this.normalizeHistoryAssetUrl(content.filePath || content.audioUrl);
      return {
        id,
        role,
        type: 'audio',
        content: {
          ...content,
          ...(filePath ? { filePath } : {}),
          playable: filePath ? content.playable !== false : false,
          duration: Number(content.duration || 0) || 0,
          transcription: String(content.transcription || content.transcript || ''),
        },
      };
    }

    const meta = message.meta && typeof message.meta === 'object' ? message.meta : null;
    const text =
      type === 'text'
        ? String(message.content || '')
        : String((message.content && typeof message.content === 'object' && message.content.text) || message.content || '');
    const textMessage = { id, role, type: 'text', content: text };

    if (role === 'assistant' && meta) {
      const attachments = this.extractTriageAttachments(meta);
      const quickReplies = this.extractQuickReplies(meta);
      if (attachments.length) textMessage.attachments = attachments;
      if (quickReplies.length) textMessage.quickReplies = quickReplies;
    }
    return textMessage;
  },

  restoreHistoryMessages(messages) {
    const restored = Array.isArray(messages)
      ? messages.map((message, index) => this.normalizeHistoryMessage(message, index)).filter(Boolean)
      : [];
    this.setData({
      messages: restored,
      scrollIntoView: restored.length ? `m_${restored.length - 1}` : '',
    });
    this.syncMessageUi();
    return restored.length > 0;
  },

  // --- assistant speech (TTS) ----------------------------------------------

  cachedSpeechUrl(messageId) {
    const id = String(messageId || '').trim();
    return id ? String(this.speechUrlMap[id] || '').trim() : '';
  },

  cacheSpeechUrl(messageId, audioUrl) {
    const id = String(messageId || '').trim();
    const url = String(audioUrl || '').trim();
    if (id && url) this.speechUrlMap[id] = url;
  },

  clearSpeechCache(messageId) {
    const id = String(messageId || '').trim();
    if (!id) return;
    if (this.data.assistantActionState.activeMessageId === id) this.stopSpeechPlayback(id);
    if (this.data.assistantActionState.loadingMessageId === id) this.setSpeechState({ loadingMessageId: '' });
    delete this.speechUrlMap[id];
  },

  setSpeechState({ activeMessageId, loadingMessageId }) {
    const current = this.data.assistantActionState || {};
    this.setData({
      assistantActionState: {
        activeMessageId: activeMessageId === undefined ? current.activeMessageId || '' : activeMessageId,
        loadingMessageId: loadingMessageId === undefined ? current.loadingMessageId || '' : loadingMessageId,
      },
    });
  },

  destroySpeechContext() {
    const context = this.speechAudioContext;
    this.speechAudioContext = null;
    if (!context) return;
    try {
      context.stop();
    } catch (_e) {
      // already stopped
    }
    try {
      context.destroy();
    } catch (_e) {
      // already destroyed
    }
  },

  createSpeechContext(messageId) {
    if (typeof wx.createInnerAudioContext !== 'function') throw new Error('当前环境不支持语音播放');
    const id = String(messageId || '').trim();
    const context = wx.createInnerAudioContext();
    context.autoplay = false;
    if ('obeyMuteSwitch' in context) context.obeyMuteSwitch = false;

    const cleanup = ({ showError = false } = {}) => {
      const state = this.data.assistantActionState || {};
      if (state.activeMessageId === id) this.setSpeechState({ activeMessageId: '' });
      if (state.loadingMessageId === id) this.setSpeechState({ loadingMessageId: '' });
      if (this.speechAudioContext === context) this.speechAudioContext = null;
      try {
        context.destroy();
      } catch (_e) {
        // already destroyed
      }
      if (showError) toast('语音播放失败');
    };

    context.onPlay(() => this.setSpeechState({ loadingMessageId: '', activeMessageId: id }));
    context.onEnded(() => cleanup());
    context.onStop(() => cleanup());
    context.onError((error) => {
      console.error('[hongwen] speech playback error', { messageId: id, error });
      cleanup({ showError: true });
    });

    this.speechAudioContext = context;
    return context;
  },

  stopSpeechPlayback(messageId = '') {
    const id = String(messageId || '').trim();
    if (id && id !== String(this.data.assistantActionState.activeMessageId || '')) return;
    this.setSpeechState({ activeMessageId: '' });
    this.destroySpeechContext();
  },

  startSpeechPlayback({ messageId, audioUrl }) {
    const id = String(messageId || '').trim();
    const url = String(audioUrl || '').trim();
    if (!id || !url) return false;

    this.destroySpeechContext();
    const context = this.createSpeechContext(id);
    this.setSpeechState({ activeMessageId: id, loadingMessageId: '' });
    try {
      context.src = url;
      context.play();
      return true;
    } catch (error) {
      this.setSpeechState({ activeMessageId: '' });
      this.destroySpeechContext();
      throw error;
    }
  },

  async fetchSpeechAudioUrl(messageId, text) {
    const id = String(messageId || '').trim();
    if (!id) return '';
    this.setSpeechState({ loadingMessageId: id });
    try {
      // https can be streamed straight into InnerAudioContext; http (the local
      // dev server) must be downloaded to a local file first.
      if (audio.supportsDirectAudioPlayback('/speak/stream')) {
        try {
          const streamResult = await api.synthesizeSpeechStream({ text, messageId: id });
          const streamUrl = request.buildAuthorizedAudioUrl(streamResult && streamResult.stream_url);
          if (streamUrl) {
            this.cacheSpeechUrl(id, streamUrl);
            return streamUrl;
          }
        } catch (error) {
          console.warn('[hongwen] speech stream fallback to buffered download', error);
        }
      }

      const result = await api.synthesizeSpeech({ text, messageId: id });
      const remoteUrl = String((result && result.audio_url) || '').trim();
      if (!remoteUrl) throw new Error('语音合成失败');
      const playablePath = await audio.prepareAudioSourceForPlayback({ audioUrl: remoteUrl, cacheKey: id });
      const normalized = String(playablePath || remoteUrl).trim();
      if (!normalized) throw new Error('音频准备失败');
      this.cacheSpeechUrl(id, normalized);
      return normalized;
    } finally {
      if (this.data.assistantActionState.loadingMessageId === id) this.setSpeechState({ loadingMessageId: '' });
    }
  },

  assistantReplyText(messageOrId) {
    const message =
      messageOrId && typeof messageOrId === 'object' ? messageOrId : this.getMessageById(messageOrId);
    if (!message || message.role !== 'assistant' || message.type !== 'text') return '';
    return String(message.content || '').trim();
  },

  async playAssistantSpeech({ messageId, message = null }) {
    const id = String(messageId || '').trim();
    if (!id) return false;
    const state = this.data.assistantActionState || {};
    if (state.loadingMessageId === id) return false;
    if (state.activeMessageId === id) {
      this.stopSpeechPlayback(id);
      return true;
    }

    const replyText = this.assistantReplyText(message || id);
    if (!replyText) {
      toast('暂无可朗读内容');
      return false;
    }
    if (state.activeMessageId) this.stopSpeechPlayback();

    try {
      let audioUrl = this.cachedSpeechUrl(id);
      if (!audioUrl) audioUrl = await this.fetchSpeechAudioUrl(id, replyText);
      if (!audioUrl) throw new Error('语音合成失败');
      return this.startSpeechPlayback({ messageId: id, audioUrl });
    } catch (error) {
      toast((error && error.message) || '语音合成失败');
      return false;
    }
  },

  // --- regenerate -----------------------------------------------------------

  async regenerateAssistantMessage(messageId) {
    const id = String(messageId || '').trim();
    if (!id || !this.data.currentSessionId) {
      toast('当前回复无法重答');
      return false;
    }
    if (id !== this.data.latestAssistantId) {
      toast('仅支持重答最新回复');
      return false;
    }
    const message = this.getMessageById(id);
    if (!message || message.role !== 'assistant' || message.type !== 'text') {
      toast('当前回复无法重答');
      return false;
    }

    const previousReplyText = this.assistantReplyText(message);
    this.clearSpeechCache(id);
    this.setData({ sending: true });
    this.syncMessageUi();
    let finalPayload = null;

    try {
      finalPayload = await api.streamRegenerateReply({
        sessionId: this.data.currentSessionId,
        messageId: id,
        onStart: (payload) => {
          if (payload && payload.session_id) this.setData({ currentSessionId: payload.session_id });
          this.setMessageContent(id, '');
        },
        onDelta: (payload) => {
          const delta = payload && payload.text != null ? String(payload.text) : '';
          if (delta) this.setMessageContent(id, this.getMessageContent(id) + delta);
        },
        onFinal: (payload) => {
          finalPayload = payload || finalPayload;
          if (payload && payload.session_id) this.setData({ currentSessionId: payload.session_id });
          const finalText = api.extractReplyText(payload) || this.getMessageContent(id);
          if (finalText) this.setMessageContent(id, finalText);
          this.setMessageMeta(id, payload && payload.meta);
        },
      });

      const replyText = api.extractReplyText(finalPayload) || this.getMessageContent(id);
      if (!replyText) throw new Error('重答失败');
      this.clearSpeechCache(id);
      toast(replyText.trim() === previousReplyText.trim() ? '已重答，结果相近' : '已重新生成');
      return true;
    } catch (error) {
      this.setMessageContent(id, previousReplyText);
      this.clearSpeechCache(id);
      toast((error && error.message) || '重答失败');
      return false;
    } finally {
      this.setData({ sending: false });
      this.syncMessageUi();
    }
  },

  // --- sending --------------------------------------------------------------

  clearPendingMedia() {
    this.setData({ pendingMediaFileId: '', pendingMediaPreviewPath: '', pendingMediaType: '' });
  },

  handleClearPendingMedia() {
    this.clearPendingMedia();
  },

  requireCustomer() {
    if (!wx.getStorageSync('external_user_id')) {
      toast('请先登录泓问账号');
      return false;
    }
    return true;
  },

  async streamReply({ type, content, autoSpeakReply = false }) {
    if (!this.requireCustomer()) return null;

    const assistantMessageId = nextId();
    this.appendMessage({ id: assistantMessageId, role: 'assistant', type: 'text', content: '' });
    this.setData({ sending: true });
    this.syncMessageUi();
    let finalPayload = null;

    try {
      finalPayload = await api.streamChatReply({
        type,
        content,
        sessionId: this.data.currentSessionId,
        onStart: (payload) => {
          if (payload && payload.session_id) this.setData({ currentSessionId: payload.session_id });
        },
        onDelta: (payload) => {
          const delta = payload && payload.text != null ? String(payload.text) : '';
          if (delta) this.setMessageContent(assistantMessageId, this.getMessageContent(assistantMessageId) + delta);
        },
        onFinal: (payload) => {
          finalPayload = payload || finalPayload;
          if (payload && payload.session_id) this.setData({ currentSessionId: payload.session_id });
          const finalText = api.extractReplyText(payload) || this.getMessageContent(assistantMessageId);
          if (finalText) this.setMessageContent(assistantMessageId, finalText);
          this.setMessageMeta(assistantMessageId, payload && payload.meta);
        },
        onError: (payload) => {
          console.error('[hongwen] stream error frame', payload);
        },
      });
    } catch (error) {
      console.error('[hongwen] stream failed', error);
      if (!this.getMessageContent(assistantMessageId)) this.removeMessage(assistantMessageId);
      toast((error && error.message) || '发送失败');
      finalPayload = null;
    } finally {
      this.setData({ sending: false });
      this.syncMessageUi();
    }

    if (finalPayload && autoSpeakReply && this.assistantReplyText(assistantMessageId)) {
      await this.playAssistantSpeech({ messageId: assistantMessageId });
    }
    return finalPayload;
  },

  async onSendText(e) {
    const text = String((e && e.detail && e.detail.text) || '').trim();
    if (!text || this.data.sending) return;
    this.appendMessage({ id: nextId(), role: 'user', type: 'text', content: text });

    if (this.data.pendingMediaFileId) {
      const reply = await this.callMixedBackend({ text, imageFileIds: [this.data.pendingMediaFileId] });
      if (reply) this.clearPendingMedia();
      return;
    }
    await this.streamReply({ type: 'text', content: { text } });
  },

  async onSendQuickReply(e) {
    if (this.data.sending) return;
    const reply = (e && e.detail) || {};
    const value = String(reply.value || '').trim();
    if (!value) return;
    const label = String(reply.label_zh || value).trim();
    this.appendMessage({ id: nextId(), role: 'user', type: 'text', content: label });
    await this.streamReply({ type: 'text', content: { text: label, quick_value: value } });
  },

  async onSendForm(e) {
    const payload = (e && e.detail) || {};
    this.appendMessage({ id: nextId(), role: 'user', type: 'form', content: payload });
    await this.streamReply({ type: 'form', content: payload });
  },

  async onAssistantAction(e) {
    const { action, messageId, message } = (e && e.detail) || {};
    if (action === 'speak') {
      await this.playAssistantSpeech({ messageId, message });
      return;
    }
    if (action === 'regenerate') {
      await this.regenerateAssistantMessage(messageId);
      return;
    }
    // like / dislike are rendered but not yet persisted server-side.
    toast('反馈功能开发中');
  },

  async onSendAudio(e) {
    const { filePath, duration } = (e && e.detail) || {};
    if (!filePath) return;
    const userMsg = {
      id: nextId(),
      role: 'user',
      type: 'audio',
      content: { filePath, duration, playable: false },
    };
    this.appendMessage(userMsg);
    await this.sendAudio(filePath, userMsg.id);
  },

  async onSendMedia(e) {
    const payload = (e && e.detail) || {};
    const filePath = payload.filePath;
    if (!filePath) return;
    const previewPath = payload.previewPath || filePath;
    const textDraft = String(payload.textDraft || '').trim();

    this.appendMessage({
      id: nextId(),
      role: 'user',
      type: 'image',
      content: { filePath, previewPath },
    });

    await this.uploadMediaAsPending(filePath, previewPath);
    if (!this.data.pendingMediaFileId) return;

    if (textDraft) {
      const reply = await this.callMixedBackend({ text: textDraft, imageFileIds: [this.data.pendingMediaFileId] });
      if (reply) this.clearPendingMedia();
    } else {
      toast('图片已附加，请发送文字或语音一起提交');
    }
  },

  async uploadMediaAsPending(filePath, previewPath = '') {
    if (!this.requireCustomer()) return;
    this.setData({ sending: true });
    this.syncMessageUi();
    try {
      const uploaded = await api.uploadFileOnly({ type: 'image', filePath });
      this.setData({
        pendingMediaFileId: uploaded.file_id,
        pendingMediaPreviewPath: previewPath || filePath,
        pendingMediaType: 'image',
      });
    } catch (_e) {
      toast('图片上传失败');
    } finally {
      this.setData({ sending: false });
      this.syncMessageUi();
    }
  },

  async transcribeVoiceMessage(filePath) {
    try {
      const result = await api.transcribeAudio({ filePath });
      return result && result.text ? String(result.text).trim() : '';
    } catch (error) {
      const statusCode = Number(error && error.statusCode);
      // A 4xx is a real rejection (bad audio, moderation) and must surface; a
      // 5xx/outage falls back to sending the raw voice note instead.
      if (statusCode >= 400 && statusCode < 500) throw error;
      return '';
    }
  },

  async sendAudio(filePath, messageId = '') {
    if (!this.requireCustomer()) return;
    this.setData({ sending: true });
    this.syncMessageUi();
    try {
      const transcript = await this.transcribeVoiceMessage(filePath);
      if (transcript) {
        if (messageId) this.setAudioTranscript(messageId, transcript);
        if (this.data.pendingMediaFileId) {
          const reply = await this.callMixedBackend({
            text: transcript,
            imageFileIds: [this.data.pendingMediaFileId],
            autoSpeakReply: true,
          });
          if (reply) this.clearPendingMedia();
        } else {
          await this.streamReply({ type: 'text', content: { text: transcript }, autoSpeakReply: true });
        }
        return;
      }

      const uploaded = await api.uploadFileOnly({ type: 'audio', filePath });
      if (this.data.pendingMediaFileId) {
        const reply = await this.callMixedBackend({
          audioFileId: uploaded.file_id,
          imageFileIds: [this.data.pendingMediaFileId],
          autoSpeakReply: true,
        });
        if (reply) this.clearPendingMedia();
      } else {
        await this.streamReply({
          type: 'audio',
          content: { audio_file_id: uploaded.file_id },
          autoSpeakReply: true,
        });
      }
    } catch (e) {
      toast((e && e.message) || '语音发送失败');
    } finally {
      this.setData({ sending: false });
      this.syncMessageUi();
    }
  },

  async callMixedBackend({ text, imageFileIds, audioFileId, autoSpeakReply = false }) {
    const content = {};
    if (text && String(text).trim()) content.text = String(text).trim();
    if (imageFileIds && imageFileIds.length) content.image_file_ids = imageFileIds;
    if (audioFileId) content.audio_file_id = audioFileId;
    return this.streamReply({ type: 'mixed', content, autoSpeakReply });
  },
});