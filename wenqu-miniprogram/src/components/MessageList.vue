<template>
  <view class="list">
    <view
      class="row"
      v-for="(msg, idx) in messages"
      :key="msg.id"
      :id="'m_' + idx"
      :class="msg.role === 'user' ? 'right' : 'left'"
    >
      <view class="stack">
        <view class="bubble" :class="msg.role === 'user' ? 'bubble-user' : 'bubble-assistant'">
          <view v-if="msg.type === 'text'">
            <text class="text">{{ msg.content }}</text>
            <view v-if="msg.role === 'assistant' && msg.content" class="ai-generated-row">
              <text class="ai-generated-tag">AI生成</text>
            </view>
          </view>

          <view v-else-if="msg.type === 'image'">
            <image class="img" :src="msg.content.filePath" mode="aspectFill"></image>
          </view>

          <view v-else-if="msg.type === 'audio'">
            <audio
              v-if="msg.content && msg.content.filePath && msg.content.playable !== false && !_isAudioBroken(msg, idx)"
              class="audio"
              :src="msg.content.filePath"
              controls
              @error="_onAudioError(msg, idx)"
            ></audio>
            <text v-else-if="_audioTranscript(msg)" class="text">{{ _audioTranscript(msg) }}</text>
            <text v-else class="text text-muted">{{ _audioFallbackText(msg) }}</text>
          </view>

          <view v-else-if="msg.type === 'form'">
            <text class="text">{{ msg.content.category }}: {{ msg.content.detail }}</text>
          </view>
        </view>

        <view v-if="msg.role === 'assistant' && msg.attachments && msg.attachments.length" class="triage-attachments">
          <view v-for="(att, aIdx) in msg.attachments" :key="'att_' + aIdx" class="triage-attachment">
            <image class="triage-attachment-img" :src="att.url" mode="widthFix" @tap="_previewAttachment(att)"></image>
            <text v-if="att.caption" class="triage-attachment-caption">{{ att.caption }}</text>
          </view>
        </view>

        <view v-if="_showQuickReplies(msg)" class="quick-replies">
          <view
            v-for="(qr, qIdx) in msg.quickReplies"
            :key="'qr_' + qIdx"
            class="quick-reply-chip"
            @tap="_emitQuickReply(qr)"
          >
            <text class="quick-reply-text">{{ qr.label_zh }}</text>
          </view>
        </view>

        <view v-if="_showAssistantActions(msg)" class="assistant-actions">
          <view
            class="action-chip"
            :class="assistantActionState.loadingMessageId === msg.id ? 'action-chip-loading' : (assistantActionState.activeMessageId === msg.id ? 'action-chip-playing' : '')"
            @tap="_emitAssistantAction('speak', msg)"
          >
            <image class="action-icon" src="/static/icons/message-action-speak.svg" mode="aspectFit"></image>
            <view
              v-if="assistantActionState.loadingMessageId === msg.id || assistantActionState.activeMessageId === msg.id"
              class="action-status-dot"
              :class="assistantActionState.loadingMessageId === msg.id ? 'action-status-dot-loading' : 'action-status-dot-playing'"
            ></view>
          </view>
          <view class="action-chip" @tap="_emitAssistantAction('like', msg)">
            <image class="action-icon" src="/static/icons/message-action-like.svg" mode="aspectFit"></image>
          </view>
          <view class="action-chip" @tap="_emitAssistantAction('dislike', msg)">
            <image class="action-icon" src="/static/icons/message-action-dislike.svg" mode="aspectFit"></image>
          </view>
          <view class="action-chip" @tap="_emitAssistantAction('regenerate', msg)">
            <image class="action-icon" src="/static/icons/message-action-regenerate.svg" mode="aspectFit"></image>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script>
export default {
  emits: ['assistant-action', 'quick-reply'],
  props: {
    messages: {
      type: Array,
      default: () => []
    },
    assistantActionState: {
      type: Object,
      default: () => ({})
    },
    latestAssistantId: {
      type: String,
      default: ''
    },
    showLatestActions: {
      type: Boolean,
      default: false
    }
  },
  data() {
    return {
      brokenAudioMap: {}
    }
  },
  methods: {
    _audioKey(msg, idx) {
      return String((msg && msg.id) || idx)
    },
    _isAudioBroken(msg, idx) {
      return !!this.brokenAudioMap[this._audioKey(msg, idx)]
    },
    _onAudioError(msg, idx) {
      this.brokenAudioMap[this._audioKey(msg, idx)] = true
    },
    _audioTranscript(msg) {
      const content = msg && msg.content ? msg.content : null
      if (!content || typeof content !== 'object') {
        return ''
      }

      return String(content.transcription || content.transcript || '').trim()
    },
    _audioFallbackText(msg) {
      const transcription = this._audioTranscript(msg)
      if (transcription) {
        return transcription
      }

      const duration = msg && msg.content ? Number(msg.content.duration || 0) : 0
      return duration > 0 ? `语音消息 (${duration}s)` : '语音消息'
    },
    _showAssistantActions(msg) {
      return Boolean(
        this.showLatestActions &&
        msg &&
        msg.role === 'assistant' &&
        msg.type === 'text' &&
        String(msg.content || '').trim() &&
        String(msg.id || '') === String(this.latestAssistantId || '')
      )
    },
    _emitAssistantAction(action, msg) {
      this.$emit('assistant-action', {
        action,
        messageId: msg && msg.id ? msg.id : '',
        message: msg || null
      })
    },
    _showQuickReplies(msg) {
      return Boolean(
        this.showLatestActions &&
        msg &&
        msg.role === 'assistant' &&
        Array.isArray(msg.quickReplies) &&
        msg.quickReplies.length &&
        String(msg.id || '') === String(this.latestAssistantId || '')
      )
    },
    _emitQuickReply(qr) {
      this.$emit('quick-reply', qr)
    },
    _previewAttachment(att) {
      const url = att && att.url ? String(att.url) : ''
      if (!url) {
        return
      }
      uni.previewImage({ urls: [url], current: url })
    }
  }
}
</script>

<style scoped>
.list {
  padding: 12rpx 0;
  background: transparent;
}

.row {
  display: flex;
  margin-bottom: 24rpx;
}

.left {
  justify-content: flex-start;
}

.right {
  justify-content: flex-end;
}

.stack {
  max-width: 86%;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.left .stack {
  align-items: flex-start;
}

.right .stack {
  align-items: flex-end;
}

.bubble {
  max-width: 100%;
  padding: 22rpx 26rpx;
  border-radius: 30rpx;
  border: 1rpx solid rgba(148, 163, 184, 0.12);
  box-shadow: 0 10rpx 24rpx rgba(15, 23, 42, 0.05);
  word-break: break-word;
  overflow: hidden;
}

.bubble-assistant {
  background: var(--assistant-bubble, rgba(255, 255, 255, 0.88));
}

.bubble-user {
  background: var(--user-bubble, #dcecff);
}

.text {
  font-size: 30rpx;
  line-height: 46rpx;
  color: #162033;
  white-space: pre-wrap;
}

.text-muted {
  color: #475569;
}

.ai-generated-row {
  margin-top: 12rpx;
}

.ai-generated-tag {
  display: inline-block;
  padding: 2rpx 12rpx;
  border: 1rpx solid rgba(100, 116, 139, 0.35);
  border-radius: 10rpx;
  font-size: 22rpx;
  line-height: 32rpx;
  color: #64748b;
}

.img {
  width: 360rpx;
  height: 360rpx;
  border-radius: 22rpx;
}

.audio {
  width: 420rpx;
}

.triage-attachments {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  max-width: 100%;
}

.triage-attachment {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.triage-attachment-img {
  width: 420rpx;
  border-radius: 20rpx;
  border: 1rpx solid rgba(148, 163, 184, 0.2);
  background: #ffffff;
}

.triage-attachment-caption {
  font-size: 24rpx;
  line-height: 34rpx;
  color: #64748b;
}

.quick-replies {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  padding: 2rpx 4rpx 0;
}

.quick-reply-chip {
  padding: 12rpx 26rpx;
  border-radius: 999rpx;
  border: 1rpx solid #4f8cff;
  background: #f2f7ff;
}

.quick-reply-text {
  font-size: 26rpx;
  line-height: 36rpx;
  color: #1f5fd0;
}

.assistant-actions {
  display: flex;
  align-items: center;
  gap: 12rpx;
  flex-wrap: wrap;
  padding: 2rpx 4rpx 0;
}

.action-chip {
  width: 48rpx;
  height: 48rpx;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999rpx;
  background: transparent;
  transition: background-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.action-chip-loading {
  background: rgba(59, 130, 246, 0.12);
  box-shadow: 0 8rpx 18rpx rgba(59, 130, 246, 0.16);
}

.action-chip-playing {
  background: rgba(59, 130, 246, 0.18);
  box-shadow: 0 10rpx 22rpx rgba(59, 130, 246, 0.22);
  transform: translateY(-1rpx);
}

.action-icon {
  width: 32rpx;
  height: 32rpx;
}

.action-status-dot {
  position: absolute;
  top: 4rpx;
  right: 4rpx;
  width: 10rpx;
  height: 10rpx;
  border-radius: 999rpx;
}

.action-status-dot-loading {
  background: #60a5fa;
  animation: actionPulse 1.2s ease-in-out infinite;
}

.action-status-dot-playing {
  background: #2563eb;
}

@keyframes actionPulse {
  0% {
    opacity: 0.45;
    transform: scale(0.82);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
  100% {
    opacity: 0.45;
    transform: scale(0.82);
  }
}
</style>

