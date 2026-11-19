// Conversation renderer for 泓问 - native rewrite of src/components/MessageList.vue.
//
// Renders the server's expanded history rows (a mixed turn arrives as separate
// user rows with :image:N / :audio / :text id suffixes), triage image
// attachments, quick-reply chips and the latest-reply action row.
//
// The deprecated <audio controls> component is replaced by a tap-to-play row
// backed by wx.createInnerAudioContext: the native audio component cannot carry
// the access token that protected uploads require, and it does not report errors
// in a way we can fall back from. A failed playback marks the row broken and the
// transcript (or a duration label) is shown instead.
//
// The scroll-view lives here rather than on the page so scroll-into-view ids
// never cross a custom-component boundary.
const audioUtil = require('../../utils/audio');

Component({
  properties: {
    messages: { type: Array, value: [] },
    assistantActionState: { type: Object, value: {} },
    latestAssistantId: { type: String, value: '' },
    showLatestActions: { type: Boolean, value: false },
    scrollIntoView: { type: String, value: '' },
  },

  data: {
    brokenAudioMap: {},
    playingAudioId: '',
  },

  lifetimes: {
    attached() {
      this.audioContext = null;
    },
    detached() {
      this.stopAudio();
    },
  },

  methods: {
    stopAudio() {
      const context = this.audioContext;
      this.audioContext = null;
      if (this.data.playingAudioId) this.setData({ playingAudioId: '' });
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

    markAudioBroken(id) {
      const broken = { ...this.data.brokenAudioMap };
      broken[id] = true;
      this.setData({ brokenAudioMap: broken });
    },

    handleAudioTap(e) {
      const id = String(e.currentTarget.dataset.id || '');
      const src = audioUtil.playableAudioSrc(e.currentTarget.dataset.src);
      if (!id || !src) return;
      if (this.data.playingAudioId === id) {
        this.stopAudio();
        return;
      }
      if (typeof wx.createInnerAudioContext !== 'function') {
        wx.showToast({ title: '当前环境不支持语音播放', icon: 'none' });
        return;
      }

      this.stopAudio();
      const context = wx.createInnerAudioContext();
      context.autoplay = false;
      if ('obeyMuteSwitch' in context) context.obeyMuteSwitch = false;

      const isCurrent = () => this.audioContext === context;
      context.onPlay(() => {
        if (isCurrent()) this.setData({ playingAudioId: id });
      });
      context.onEnded(() => {
        if (isCurrent()) this.stopAudio();
      });
      context.onStop(() => {
        if (isCurrent()) this.stopAudio();
      });
      context.onError((error) => {
        console.error('[hongwen] voice note playback error', { id, error });
        if (!isCurrent()) return;
        this.markAudioBroken(id);
        this.stopAudio();
        wx.showToast({ title: '语音播放失败', icon: 'none' });
      });

      this.audioContext = context;
      context.src = src;
      context.play();
    },

    previewUrl(url) {
      const value = String(url || '');
      if (!value) return;
      wx.previewImage({ urls: [value], current: value });
    },

    handlePreviewImage(e) {
      this.previewUrl(e.currentTarget.dataset.url);
    },

    handleQuickReply(e) {
      const { label, value } = e.currentTarget.dataset;
      this.triggerEvent('quick-reply', { label_zh: String(label || value || ''), value: String(value || '') });
    },

    handleAssistantAction(e) {
      const action = String(e.currentTarget.dataset.action || '');
      const messageId = String(e.currentTarget.dataset.id || '');
      const message = this.data.messages.find((item) => String(item.id || '') === messageId) || null;
      this.triggerEvent('assistant-action', { action, messageId, message });
    },
  },
});