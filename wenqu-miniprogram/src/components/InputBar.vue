<template>
  <view class="composer-shell">
    <view v-if="voiceMode" class="record-window">
      <view class="record-overlay">
        <view class="record-overlay-bg" :style="{ opacity: recordBackdropOpacity }">
        </view>

        <view class="record-action record-cancel" @tap="cancelVoiceRecord">
          <text class="record-action-icon">×</text>
        </view>

        <view class="record-center">
          <view class="meter-track">
            <view
              v-for="bar in meterBars"
              :key="bar.id"
              class="meter-bar"
              :style="{ height: bar.height, opacity: bar.opacity, transform: bar.transform }"
            ></view>
          </view>
          <view class="record-meta">
            <text class="record-label">{{ recording ? '正在聆听' : '准备发送' }}</text>
            <text class="record-time">{{ recordingDurationLabel }}</text>
          </view>
        </view>

        <view
          class="record-action record-send"
          :class="{ disabled: !recording || sending }"
          @tap="finishVoiceRecord"
        >
          <text class="record-action-icon">↑</text>
        </view>
      </view>
    </view>

    <view v-else class="composer-window">
      <view class="input-window">
        <view class="composer-overlay-bg">
        </view>

        <textarea
          class="text-input"
          v-model="text"
          confirm-type="send"
          auto-height
          maxlength="-1"
          show-confirm-bar="false"
          placeholder="请输入信息"
          placeholder-class="text-placeholder"
          @confirm="emitText"
        ></textarea>
      </view>

      <view class="entry-button media-entry" @tap="openMore">
        <view class="icon-plus">
          <view class="icon-plus-horizontal"></view>
          <view class="icon-plus-vertical"></view>
        </view>
      </view>

      <view v-if="!hasTextDraft" class="entry-button voice-entry" @tap="toggleVoiceMode">
        <view class="icon-voice">
          <view class="icon-voice-bar icon-voice-bar-a"></view>
          <view class="icon-voice-bar icon-voice-bar-b"></view>
          <view class="icon-voice-bar icon-voice-bar-c"></view>
          <view class="icon-voice-bar icon-voice-bar-d"></view>
        </view>
      </view>

      <view
        v-if="hasTextDraft"
        class="send-button"
        :class="{ active: hasTextDraft && !sending, disabled: !hasTextDraft || sending }"
        @tap="emitText"
      >
        <view class="icon-send">
          <view class="icon-send-stem"></view>
          <view class="icon-send-head icon-send-head-left"></view>
          <view class="icon-send-head icon-send-head-right"></view>
        </view>
      </view>
    </view>
  </view>
</template>

<script>
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
  { baseHeight: 8, peakHeight: 22 }
]

function createMeterBars() {
  return METER_BAR_PRESETS.map((preset, index) => ({
    id: index + 1,
    baseHeight: preset.baseHeight,
    peakHeight: preset.peakHeight,
    height: `${preset.baseHeight}rpx`,
    opacity: 0.24,
    transform: 'translateY(4rpx)'
  }))
}

export default {
  emits: ['send-text', 'send-audio', 'send-image', 'send-form', 'send-media'],
  props: {
    sending: {
      type: Boolean,
      default: false
    }
  },
  data() {
    return {
      voiceMode: false,
      text: '',
      recording: false,
      recorder: null,
      recorderSessionToken: 0,
      recentMeterLengths: [],
      meterBars: createMeterBars(),
      voiceLevel: 0,
      recordBackdropOpacity: 0.54,
      recordBackdropTransform: 'scale(1)',
      recordingStartedAt: 0,
      recordingDurationMs: 0,
      recordingTimer: null,
      recordAction: 'idle'
    }
  },
  computed: {
    hasTextDraft() {
      return Boolean(String(this.text || '').trim())
    },
    recordingDurationLabel() {
      const totalSeconds = Math.max(0, Math.floor(this.recordingDurationMs / 1000))
      const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, '0')
      const seconds = String(totalSeconds % 60).padStart(2, '0')
      return `${minutes}:${seconds}`
    }
  },
  beforeUnmount() {
    this._stopDurationTicker()
    this.recorderSessionToken += 1
  },
  methods: {
    toggleVoiceMode() {
      if (this.voiceMode || this.recording || this.sending) {
        return
      }
      this.startImmediateRecording()
    },
    _persistRecordedFile(tempFilePath) {
      if (!tempFilePath || typeof uni.saveFile !== 'function') {
        return Promise.resolve(tempFilePath || '')
      }

      return new Promise((resolve) => {
        uni.saveFile({
          tempFilePath,
          success: (res) => {
            resolve(res.savedFilePath || tempFilePath)
          },
          fail: () => {
            resolve(tempFilePath)
          }
        })
      })
    },
    _getRecorderManager() {
      if (!this.recorder) {
        this.recorder = uni.getRecorderManager()
      }
      return this.recorder
    },
    _createRecorderSession() {
      this.recorderSessionToken += 1
      return this.recorderSessionToken
    },
    _bindRecorderStop(sessionToken, handler) {
      const manager = this._getRecorderManager()
      if (typeof manager.offStop === 'function') {
        manager.offStop()
      }
      manager.onStop((res) => {
        if (sessionToken !== this.recorderSessionToken) {
          return
        }
        handler(res)
      })
      return manager
    },
    _bindRecorderFrames(sessionToken, handler) {
      const manager = this._getRecorderManager()
      if (typeof manager.offFrameRecorded === 'function') {
        manager.offFrameRecorded()
      }
      if (typeof manager.onFrameRecorded === 'function') {
        manager.onFrameRecorded((res) => {
          if (sessionToken !== this.recorderSessionToken) {
            return
          }
          handler(res)
        })
      }
      return manager
    },
    _bindRecorderError(sessionToken, handler) {
      const manager = this._getRecorderManager()
      if (typeof manager.offError === 'function') {
        manager.offError()
      }
      if (typeof manager.onError === 'function') {
        manager.onError((error) => {
          if (sessionToken !== this.recorderSessionToken) {
            return
          }
          handler(error)
        })
      }
      return manager
    },
    _startDurationTicker() {
      this._stopDurationTicker()
      this.recordingTimer = setInterval(() => {
        if (!this.recordingStartedAt) {
          this.recordingDurationMs = 0
          return
        }
        this.recordingDurationMs = Date.now() - this.recordingStartedAt
      }, 200)
    },
    _stopDurationTicker() {
      if (this.recordingTimer) {
        clearInterval(this.recordingTimer)
        this.recordingTimer = null
      }
    },
    _syncRecordBackdrop(level) {
      const normalizedLevel = Math.max(0, Math.min(1, Number(level) || 0))
      this.recordBackdropOpacity = 0.52 + normalizedLevel * 0.2
      this.recordBackdropTransform = `scale(${(1 + normalizedLevel * 0.12).toFixed(3)})`
    },
    _syncMeterBars(level) {
      const normalizedLevel = Math.max(0, Math.min(1, Number(level) || 0))
      const easedLevel = Math.pow(normalizedLevel, 0.82)
      const totalBars = this.meterBars.length
      const centerIndex = (totalBars - 1) / 2

      this.meterBars = this.meterBars.map((bar) => {
        const distanceFromCenter = Math.abs(centerIndex - (bar.id - 1))
        const idleIntensity = Math.max(0.02, 0.085 - distanceFromCenter * 0.011)
        const signalIntensity = Math.max(0, easedLevel * 1.18 - distanceFromCenter * 0.13)
        const intensity = Math.min(1, Math.max(idleIntensity, signalIntensity))
        const baseHeight = Number(bar.baseHeight || 16)
        const peakHeight = Number(bar.peakHeight || baseHeight)
        const height = baseHeight + (peakHeight - baseHeight) * intensity
        const opacity = 0.18 + intensity * 0.72
        const translateY = (1 - intensity) * 4.2

        return {
          ...bar,
          height: `${Math.round(height)}rpx`,
          opacity,
          transform: `translateY(${translateY.toFixed(1)}rpx)`
        }
      })
    },
    _resolveVoiceLevel(frameBuffer) {
      const nextLevel = this._estimateVoiceLevel(frameBuffer)
      this.voiceLevel = nextLevel
      this._syncRecordBackdrop(nextLevel)
      this._syncMeterBars(nextLevel)
    },
    _estimateVoiceLevel(frameBuffer) {
      if (!frameBuffer) {
        return Math.max(0, this.voiceLevel * 0.84)
      }

      const bytes = new Uint8Array(frameBuffer)
      if (!bytes.length) {
        return Math.max(0, this.voiceLevel * 0.84)
      }

      // For WeChat mp3 frame callbacks, louder speech tends to produce longer chunks,
      // while silence often collapses into repeated sentinel bytes like 85 / 170.
      let repeatedCount = 0
      let longestRun = 0
      let currentRun = 0
      let previousValue = -1

      for (let index = 0; index < bytes.length; index += 1) {
        const value = bytes[index]
        if (value === previousValue) {
          currentRun += 1
        } else {
          previousValue = value
          currentRun = 1
        }

        if (value === 85 || value === 170) {
          repeatedCount += 1
        }

        if (currentRun > longestRun) {
          longestRun = currentRun
        }
      }

      const silenceRatio = repeatedCount / bytes.length
      const runRatio = longestRun / bytes.length

      if (silenceRatio > 0.84 || runRatio > 0.24) {
        this.recentMeterLengths = []
        return Math.max(0, this.voiceLevel * 0.8)
      }

      this.recentMeterLengths.push(bytes.length)
      if (this.recentMeterLengths.length > 18) {
        this.recentMeterLengths.shift()
      }

      let minLength = this.recentMeterLengths[0] || bytes.length
      let maxLength = minLength
      for (let index = 1; index < this.recentMeterLengths.length; index += 1) {
        const value = this.recentMeterLengths[index]
        if (value < minLength) {
          minLength = value
        }
        if (value > maxLength) {
          maxLength = value
        }
      }

      const activationFloor = minLength + 220
      const activationRange = Math.max(1200, maxLength - minLength + 420)
      const normalizedLength = Math.max(0, Math.min(1, (bytes.length - activationFloor) / activationRange))
      const opennessScore = Math.max(0, Math.min(1, 1 - silenceRatio * 0.64 - runRatio * 0.42))
      const activityScore = Math.max(0, Math.min(1, normalizedLength * 0.9 + opennessScore * 0.1))
      const liftedScore = Math.max(activityScore * 0.42, Math.pow(activityScore, 1.68))

      if (liftedScore >= this.voiceLevel) {
        return Math.max(0, Math.min(1, this.voiceLevel * 0.72 + liftedScore * 0.28))
      }

      return Math.max(0, Math.min(1, this.voiceLevel * 0.84 + liftedScore * 0.16))
    },
    _resetVoiceMode() {
      this.voiceMode = false
      this.recording = false
      this.recentMeterLengths = []
      this.voiceLevel = 0
      this._syncRecordBackdrop(0)
      this._syncMeterBars(0)
      this.recordAction = 'idle'
      this.recordingStartedAt = 0
      this.recordingDurationMs = 0
      this._stopDurationTicker()
    },
    async startImmediateRecording() {
      if (this.recording || this.sending) {
        return
      }

      this.voiceMode = true
      this.recordAction = 'idle'
      this.voiceLevel = 0
      this._syncRecordBackdrop(0)
      this._syncMeterBars(0)
      this.recordingStartedAt = Date.now()
      this.recordingDurationMs = 0

      const sessionToken = this._createRecorderSession()
      const manager = this._getRecorderManager()

      try {
        this._bindRecorderStop(sessionToken, async (res) => {
          const action = this.recordAction
          const elapsedMs = this.recordingStartedAt ? (Date.now() - this.recordingStartedAt) : 0
          const duration = Math.max(1, Math.round(elapsedMs / 1000))
          const tempFilePath = res && res.tempFilePath ? res.tempFilePath : ''

          this.recording = false
          this._stopDurationTicker()

          if (action === 'send' && tempFilePath) {
            const filePath = await this._persistRecordedFile(tempFilePath)
            this.$emit('send-audio', { filePath, duration })
          }

          this._resetVoiceMode()
        })

        this._bindRecorderFrames(sessionToken, (res) => {
          this._resolveVoiceLevel(res && res.frameBuffer)
        })

        this._bindRecorderError(sessionToken, () => {
          this._resetVoiceMode()
          uni.showToast({ title: '录音失败', icon: 'none' })
        })

        this.recording = true
        this._startDurationTicker()
        manager.start({
          duration: 600000,
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 64000,
          format: 'mp3',
          frameSize: 1
        })
      } catch (error) {
        this._resetVoiceMode()
        uni.showToast({ title: '录音失败', icon: 'none' })
      }
    },
    cancelVoiceRecord() {
      if (!this.recorder) {
        this._resetVoiceMode()
        return
      }
      this.recordAction = 'cancel'
      try {
        this.recorder.stop()
      } catch (error) {
        this._resetVoiceMode()
      }
    },
    finishVoiceRecord() {
      if (!this.recorder || !this.recording || this.sending) {
        return
      }
      this.recordAction = 'send'
      try {
        this.recorder.stop()
      } catch (error) {
        this._resetVoiceMode()
        uni.showToast({ title: '录音失败', icon: 'none' })
      }
    },
    emitText() {
      const value = this.text.trim()
      if (!value || this.sending) {
        return
      }
      this.$emit('send-text', value)
      this.text = ''
    },
    openMore() {
      const that = this
      uni.showActionSheet({
        itemList: ['从相册选择图片', '拍照'],
        success(res) {
          if (res.tapIndex === 0) {
            that.chooseMedia('album')
          } else if (res.tapIndex === 1) {
            that.chooseMedia('camera')
          }
        }
      })
    },
    chooseMedia(source) {
      uni.chooseMedia({
        count: 1,
        // Video is image-only for now: WeChat's video check is asynchronous, so a clip could not
        // be moderated before it reached the agent, and the UGC declaration has to match.
        mediaType: ['image'],
        sourceType: [source],
        camera: 'back',
        success: (res) => {
          const file = res.tempFiles && res.tempFiles[0]
          if (!file) {
            return
          }

          const filePath = file.tempFilePath
          const fileType = file.fileType
          const textDraft = (this.text || '').trim()

          this.$emit('send-media', { filePath, fileType, textDraft })
          if (textDraft) {
            this.text = ''
          }
        }
      })
    }
  }
}
</script>

<style scoped>
.composer-shell {
  width: 100%;
}

.composer-window,
.record-window {
  display: flex;
  align-items: center;
  gap: 14rpx;
  padding: 16rpx;
  border-radius: 34rpx;
}

.composer-window {
  position: relative;
  min-height: 54rpx;
  gap: 12rpx;
  padding: 20rpx 18rpx;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(31, 41, 55, 0.78) 0%, rgba(15, 23, 42, 0.9) 100%);
  box-shadow: 0 24rpx 48rpx rgba(15, 23, 42, 0.16);
}

.record-window {
  position: relative;
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
}

.record-overlay {
  position: relative;
  width: 100%;
  min-height: 108rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
  padding: 18rpx 18rpx 16rpx;
  overflow: hidden;
  border-radius: 999rpx;
  background: linear-gradient(180deg, rgba(31, 41, 55, 0.78) 0%, rgba(15, 23, 42, 0.9) 100%);
  box-shadow: 0 24rpx 48rpx rgba(15, 23, 42, 0.16);
}

.record-overlay-bg {
  position: absolute;
  left: 50%;
  top: 14rpx;
  bottom: 14rpx;
  width: 58%;
  transform: translateX(-50%);
  border-radius: 999rpx;
  background: linear-gradient(90deg, rgba(96, 165, 250, 0.12) 0%, rgba(125, 211, 252, 0.22) 50%, rgba(96, 165, 250, 0.12) 100%);
}

.composer-overlay-bg {
  position: absolute;
  inset: 0;
  border-radius: 16rpx;
  background: linear-gradient(90deg, rgba(96, 165, 250, 0.1) 0%, rgba(125, 211, 252, 0.18) 52%, rgba(96, 165, 250, 0.1) 100%);
}

.entry-button,
.record-action,
.send-button {
  position: relative;
  z-index: 2;
  width: 64rpx;
  height: 64rpx;
  border-radius: 64rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.record-action {
  background: rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(14rpx);
}

.record-action-icon {
  font-size: 38rpx;
  line-height: 1;
  color: rgba(255, 255, 255, 0.92);
}

.entry-button {
  background: rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(14rpx);
}

.voice-entry,
.media-entry {
  border: 1rpx solid rgba(255, 255, 255, 0.08);
}

.input-window {
  position: relative;
  z-index: 2;
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: stretch;
  min-height: 64rpx;
}

.text-input {
  position: relative;
  z-index: 1;
  flex: 1;
  width: 100%;
  min-height: 64rpx;
  max-height: 216rpx;
  padding: 14rpx 18rpx 14rpx 16rpx;
  box-sizing: border-box;
  font-size: 29rpx;
  line-height: 37rpx;
  color: rgba(255, 255, 255, 0.92);
  overflow-y: auto;
}

.text-placeholder {
  color: rgba(226, 232, 240, 0.5);
}

.send-button {
  background: rgba(148, 163, 184, 0.34);
  box-shadow: none;
}

.send-button.active {
  background: linear-gradient(135deg, rgba(96, 165, 250, 0.94) 0%, rgba(37, 99, 235, 1) 100%);
  box-shadow: 0 12rpx 28rpx rgba(37, 99, 235, 0.34);
}

.send-button.disabled {
  background: rgba(148, 163, 184, 0.34);
  box-shadow: none;
}

.record-send {
  background: linear-gradient(135deg, rgba(96, 165, 250, 0.94) 0%, rgba(37, 99, 235, 1) 100%);
  box-shadow: 0 12rpx 28rpx rgba(37, 99, 235, 0.34);
}

.record-send.disabled {
  background: rgba(148, 163, 184, 0.34);
  box-shadow: none;
}

.record-center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10rpx;
  padding: 0 4rpx;
  position: relative;
  z-index: 2;
}

.record-meta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
}

.record-label {
  font-size: 22rpx;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.88);
}

.record-time {
  font-size: 22rpx;
  color: rgba(255, 255, 255, 0.54);
}

.meter-track {
  width: 188rpx;
  height: 56rpx;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 6rpx;
  padding: 0;
}

.meter-bar {
  width: 8rpx;
  min-height: 8rpx;
  border-radius: 999rpx;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94) 0%, rgba(191, 219, 254, 0.84) 100%);
  box-shadow: 0 4rpx 12rpx rgba(148, 163, 184, 0.18);
  transition: height 120ms ease, opacity 120ms ease, transform 120ms ease;
}

.icon-voice {
  width: 26rpx;
  height: 24rpx;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 4rpx;
}

.icon-voice-bar {
  width: 4rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.9);
}

.icon-voice-bar-a {
  height: 10rpx;
  opacity: 0.66;
}

.icon-voice-bar-b {
  height: 18rpx;
}

.icon-voice-bar-c {
  height: 22rpx;
}

.icon-voice-bar-d {
  height: 14rpx;
  opacity: 0.74;
}

.icon-plus {
  position: relative;
  width: 22rpx;
  height: 22rpx;
}

.icon-plus-horizontal,
.icon-plus-vertical {
  position: absolute;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.92);
}

.icon-plus-horizontal {
  left: 0;
  top: 50%;
  width: 22rpx;
  height: 3rpx;
  transform: translateY(-50%);
}

.icon-plus-vertical {
  left: 50%;
  top: 0;
  width: 3rpx;
  height: 22rpx;
  transform: translateX(-50%);
}

.icon-send {
  position: relative;
  width: 24rpx;
  height: 24rpx;
}

.icon-send-stem,
.icon-send-head {
  position: absolute;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.6);
}

.send-button.active .icon-send-stem,
.send-button.active .icon-send-head {
  background: #ffffff;
}

.icon-send-stem {
  left: 10rpx;
  top: 3rpx;
  width: 4rpx;
  height: 18rpx;
}

.icon-send-head {
  top: 3rpx;
  width: 13rpx;
  height: 4rpx;
}

.icon-send-head-left {
  left: 1rpx;
  transform: rotate(-45deg);
  transform-origin: right center;
}

.icon-send-head-right {
  left: 10rpx;
  transform: rotate(45deg);
  transform-origin: left center;
}
</style>

