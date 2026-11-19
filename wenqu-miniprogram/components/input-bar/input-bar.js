// Composer for 问渠 - native rewrite of src/components/InputBar.vue.
//
// Text entry, image attachment (compressed through utils/image.js) and voice
// capture. Tapping the voice entry starts recording immediately and swaps the
// composer for a full-width record window with a live level meter; the left
// action cancels, the right action sends.
//
// Recording uses wx.getRecorderManager (mp3, 16 kHz mono, frameSize 1 KB so
// onFrameRecorded drives the meter). Every callback is guarded by a session
// token: WeChat's recorder manager is a singleton, so a cancelled session's
// late onStop must not emit a second voice note.
const image = require('../../utils/image');

const METER_BAR_PRESETS = [
  { baseHeight: 8, peakHeight: 22 },
  { baseHeight: 10, peakHeight: 28 },
  { baseHeight: 12, peakHeight: 36 },
  { baseHeight: 14, peakHeight: 46 },
  { baseHeight: 17, peakHeight: 58 },
  { baseHeight: 22, peakHeight: 72 },
  { baseHeight: 22, peakHeight: 72 },
  { baseHeight: 17, peakHeight: 58 },
  { baseHeight: 14, peakHeight: 46 },
  { baseHeight: 12, peakHeight: 36 },
  { baseHeight: 10, peakHeight: 28 },
  { baseHeight: 8, peakHeight: 22 },
];

function createMeterBars() {
  return METER_BAR_PRESETS.map((preset, index) => ({
    id: index + 1,
    baseHeight: preset.baseHeight,
    peakHeight: preset.peakHeight,
    height: `${preset.baseHeight}rpx`,
    opacity: 0.24,
    transform: 'translateY(4rpx)',
  }));
}

function durationLabel(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, '0');
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

Component({
  properties: {
    sending: { type: Boolean, value: false },
  },

  data: {
    voiceMode: false,
    text: '',
    hasTextDraft: false,
    recording: false,
    meterBars: createMeterBars(),
    recordBackdropOpacity: 0.54,
    recordingDurationLabel: '00:00',
  },

  lifetimes: {
    attached() {
      this.recorder = null;
      this.recorderSessionToken = 0;
      this.recentMeterLengths = [];
      this.voiceLevel = 0;
      this.recordingStartedAt = 0;
      this.recordingTimer = null;
      this.recordAction = 'idle';
    },
    detached() {
      this.stopDurationTicker();
      this.recorderSessionToken += 1;
      if (this.recorder && this.data.recording) {
        try {
          this.recorder.stop();
        } catch (_e) {
          // recorder already idle
        }
      }
    },
  },

  methods: {
    // --- text ---------------------------------------------------------------

    handleInput(e) {
      const text = String((e.detail && e.detail.value) || '');
      this.setData({ text, hasTextDraft: Boolean(text.trim()) });
    },

    emitText() {
      const value = this.data.text.trim();
      if (!value || this.data.sending) return;
      this.triggerEvent('send-text', { text: value });
      this.setData({ text: '', hasTextDraft: false });
    },

    // --- image --------------------------------------------------------------

    openMore() {
      wx.showActionSheet({
        itemList: ['从相册选择图片', '拍照'],
        success: (res) => {
          if (res.tapIndex === 0) this.chooseMedia('album');
          else if (res.tapIndex === 1) this.chooseMedia('camera');
        },
      });
    },

    async chooseMedia(source) {
      const textDraft = this.data.text.trim();
      try {
        // pickPhotos compresses and persists; a user cancel rejects and is a no-op.
        const paths = await image.pickPhotos({ count: 1, sourceType: [source] });
        const filePath = paths && paths[0];
        if (!filePath) return;
        this.triggerEvent('send-media', { filePath, previewPath: filePath, textDraft });
        if (textDraft) this.setData({ text: '', hasTextDraft: false });
      } catch (_e) {
        // cancelled picker
      }
    },

    // --- recorder plumbing --------------------------------------------------

    getRecorderManager() {
      if (!this.recorder) this.recorder = wx.getRecorderManager();
      return this.recorder;
    },

    createRecorderSession() {
      this.recorderSessionToken += 1;
      return this.recorderSessionToken;
    },

    bindRecorderStop(sessionToken, handler) {
      const manager = this.getRecorderManager();
      if (typeof manager.offStop === 'function') manager.offStop();
      manager.onStop((res) => {
        if (sessionToken !== this.recorderSessionToken) return;
        handler(res);
      });
      return manager;
    },

    bindRecorderFrames(sessionToken, handler) {
      const manager = this.getRecorderManager();
      if (typeof manager.offFrameRecorded === 'function') manager.offFrameRecorded();
      if (typeof manager.onFrameRecorded === 'function') {
        manager.onFrameRecorded((res) => {
          if (sessionToken !== this.recorderSessionToken) return;
          handler(res);
        });
      }
      return manager;
    },

    bindRecorderError(sessionToken, handler) {
      const manager = this.getRecorderManager();
      if (typeof manager.offError === 'function') manager.offError();
      if (typeof manager.onError === 'function') {
        manager.onError((error) => {
          if (sessionToken !== this.recorderSessionToken) return;
          handler(error);
        });
      }
      return manager;
    },

    startDurationTicker() {
      this.stopDurationTicker();
      this.recordingTimer = setInterval(() => {
        if (!this.recordingStartedAt) {
          this.setData({ recordingDurationLabel: '00:00' });
          return;
        }
        this.setData({ recordingDurationLabel: durationLabel(Date.now() - this.recordingStartedAt) });
      }, 200);
    },

    stopDurationTicker() {
      if (this.recordingTimer) {
        clearInterval(this.recordingTimer);
        this.recordingTimer = null;
      }
    },

    persistRecordedFile(tempFilePath) {
      if (!tempFilePath) return Promise.resolve('');
      return new Promise((resolve) => {
        wx.getFileSystemManager().saveFile({
          tempFilePath,
          success: (res) => resolve(res.savedFilePath || tempFilePath),
          fail: () => resolve(tempFilePath),
        });
      });
    },

    // --- level meter --------------------------------------------------------

    syncRecordBackdrop(level) {
      const normalized = Math.max(0, Math.min(1, Number(level) || 0));
      this.setData({ recordBackdropOpacity: 0.52 + normalized * 0.2 });
    },

    syncMeterBars(level) {
      const normalized = Math.max(0, Math.min(1, Number(level) || 0));
      const easedLevel = Math.pow(normalized, 0.82);
      const bars = this.data.meterBars;
      const centerIndex = (bars.length - 1) / 2;

      const next = bars.map((bar) => {
        const distanceFromCenter = Math.abs(centerIndex - (bar.id - 1));
        const idleIntensity = Math.max(0.02, 0.085 - distanceFromCenter * 0.011);
        const signalIntensity = Math.max(0, easedLevel * 1.18 - distanceFromCenter * 0.13);
        const intensity = Math.min(1, Math.max(idleIntensity, signalIntensity));
        const baseHeight = Number(bar.baseHeight || 16);
        const peakHeight = Number(bar.peakHeight || baseHeight);
        const height = baseHeight + (peakHeight - baseHeight) * intensity;
        return {
          ...bar,
          height: `${Math.round(height)}rpx`,
          opacity: 0.18 + intensity * 0.72,
          transform: `translateY(${((1 - intensity) * 4.2).toFixed(1)}rpx)`,
        };
      });
      this.setData({ meterBars: next });
    },

    resolveVoiceLevel(frameBuffer) {
      const nextLevel = this.estimateVoiceLevel(frameBuffer);
      this.voiceLevel = nextLevel;
      this.syncRecordBackdrop(nextLevel);
      this.syncMeterBars(nextLevel);
    },

    // WeChat hands us encoded mp3 frames, not PCM, so there is no amplitude to
    // read. Louder speech produces longer chunks; silence collapses into
    // repeated sentinel bytes (85 / 170). Chunk length relative to the recent
    // window, minus a silence penalty, is the level proxy.
    estimateVoiceLevel(frameBuffer) {
      if (!frameBuffer) return Math.max(0, this.voiceLevel * 0.84);
      const bytes = new Uint8Array(frameBuffer);
      if (!bytes.length) return Math.max(0, this.voiceLevel * 0.84);

      let repeatedCount = 0;
      let longestRun = 0;
      let currentRun = 0;
      let previousValue = -1;

      for (let index = 0; index < bytes.length; index += 1) {
        const value = bytes[index];
        if (value === previousValue) currentRun += 1;
        else {
          previousValue = value;
          currentRun = 1;
        }
        if (value === 85 || value === 170) repeatedCount += 1;
        if (currentRun > longestRun) longestRun = currentRun;
      }

      const silenceRatio = repeatedCount / bytes.length;
      const runRatio = longestRun / bytes.length;

      if (silenceRatio > 0.84 || runRatio > 0.24) {
        this.recentMeterLengths = [];
        return Math.max(0, this.voiceLevel * 0.8);
      }

      this.recentMeterLengths.push(bytes.length);
      if (this.recentMeterLengths.length > 18) this.recentMeterLengths.shift();

      let minLength = this.recentMeterLengths[0] || bytes.length;
      let maxLength = minLength;
      for (let index = 1; index < this.recentMeterLengths.length; index += 1) {
        const value = this.recentMeterLengths[index];
        if (value < minLength) minLength = value;
        if (value > maxLength) maxLength = value;
      }

      const activationFloor = minLength + 220;
      const activationRange = Math.max(1200, maxLength - minLength + 420);
      const normalizedLength = Math.max(0, Math.min(1, (bytes.length - activationFloor) / activationRange));
      const opennessScore = Math.max(0, Math.min(1, 1 - silenceRatio * 0.64 - runRatio * 0.42));
      const activityScore = Math.max(0, Math.min(1, normalizedLength * 0.9 + opennessScore * 0.1));
      const liftedScore = Math.max(activityScore * 0.42, Math.pow(activityScore, 1.68));

      if (liftedScore >= this.voiceLevel) {
        return Math.max(0, Math.min(1, this.voiceLevel * 0.72 + liftedScore * 0.28));
      }
      return Math.max(0, Math.min(1, this.voiceLevel * 0.84 + liftedScore * 0.16));
    },

    // --- record window ------------------------------------------------------

    resetVoiceMode() {
      this.stopDurationTicker();
      this.recentMeterLengths = [];
      this.voiceLevel = 0;
      this.recordingStartedAt = 0;
      this.recordAction = 'idle';
      this.setData({
        voiceMode: false,
        recording: false,
        meterBars: createMeterBars(),
        recordBackdropOpacity: 0.54,
        recordingDurationLabel: '00:00',
      });
    },

    toggleVoiceMode() {
      if (this.data.voiceMode || this.data.recording || this.data.sending) return;
      this.startImmediateRecording();
    },

    startImmediateRecording() {
      if (this.data.recording || this.data.sending) return;

      this.voiceLevel = 0;
      this.recentMeterLengths = [];
      this.recordingStartedAt = Date.now();
      this.recordAction = 'idle';
      this.setData({
        voiceMode: true,
        recording: true,
        meterBars: createMeterBars(),
        recordBackdropOpacity: 0.54,
        recordingDurationLabel: '00:00',
      });

      const sessionToken = this.createRecorderSession();
      const manager = this.getRecorderManager();

      try {
        this.bindRecorderStop(sessionToken, async (res) => {
          const action = this.recordAction;
          const elapsedMs = this.recordingStartedAt ? Date.now() - this.recordingStartedAt : 0;
          const duration = Math.max(1, Math.round(elapsedMs / 1000));
          const tempFilePath = (res && res.tempFilePath) || '';

          this.stopDurationTicker();
          this.setData({ recording: false });

          if (action === 'send' && tempFilePath) {
            const filePath = await this.persistRecordedFile(tempFilePath);
            this.triggerEvent('send-audio', { filePath, duration });
          }
          this.resetVoiceMode();
        });

        this.bindRecorderFrames(sessionToken, (res) => {
          this.resolveVoiceLevel(res && res.frameBuffer);
        });

        this.bindRecorderError(sessionToken, () => {
          this.resetVoiceMode();
          wx.showToast({ title: '录音失败', icon: 'none' });
        });

        this.startDurationTicker();
        manager.start({
          duration: 600000,
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 64000,
          format: 'mp3',
          frameSize: 1,
        });
      } catch (_error) {
        this.resetVoiceMode();
        wx.showToast({ title: '录音失败', icon: 'none' });
      }
    },

    cancelVoiceRecord() {
      if (!this.recorder) {
        this.resetVoiceMode();
        return;
      }
      this.recordAction = 'cancel';
      try {
        this.recorder.stop();
      } catch (_error) {
        this.resetVoiceMode();
      }
    },

    finishVoiceRecord() {
      if (!this.recorder || !this.data.recording || this.data.sending) return;
      this.recordAction = 'send';
      try {
        this.recorder.stop();
      } catch (_error) {
        this.resetVoiceMode();
        wx.showToast({ title: '录音失败', icon: 'none' });
      }
    },
  },
});