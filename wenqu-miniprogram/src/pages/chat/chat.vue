<template>
  <view class="page">
    <view class="chat-header-spacer" :style="chatHeaderSpacerStyle"></view>
    <view class="chat-header" :style="chatHeaderStyle">
      <view class="chat-header-side">
        <view class="menu-trigger" @tap="toggleMenu">
          <view class="menu-trigger-line"></view>
          <view class="menu-trigger-line"></view>
          <view class="menu-trigger-line"></view>
        </view>
      </view>
      <text class="chat-header-title">Helpdesk</text>
      <view class="chat-header-side"></view>
    </view>

    <view v-if="menuOpen" class="sidebar-mask" @tap="closeMenu">
      <view class="sidebar-drawer" :style="sidebarDrawerStyle" @tap.stop="noop">
        <view class="sidebar-shell">
          <scroll-view class="sidebar-scroll" scroll-y="true">
            <view class="sidebar-section sidebar-section-primary">
              <view class="sidebar-action" @tap="handleCreateNewSession">
                <view class="sidebar-action-icon">
                  <text class="sidebar-action-icon-mark">+</text>
                </view>
                <view class="sidebar-action-copy">
                  <text class="sidebar-action-title">新对话</text>
                </view>
              </view>
            </view>

            <view class="sidebar-section">
              <text class="sidebar-section-title">历史对话</text>

              <view v-if="sessionsLoading && !sessionSummaries.length" class="sidebar-history-state">
                正在加载历史对话...
              </view>
              <view v-else-if="sessionsError && !sessionSummaries.length" class="sidebar-history-state sidebar-history-state-error">
                {{ sessionsError }}
              </view>
              <view v-else-if="!sessionSummaries.length" class="sidebar-history-state">
                暂无历史对话
              </view>
              <view v-else class="sidebar-history-list">
                <view
                  v-for="session in sessionSummaries"
                  :key="session.session_id"
                  class="session-item"
                  :class="String(session.session_id || '') === String(currentSessionId || '') ? 'session-item-active' : ''"
                >
                  <view class="session-item-main" @tap="handleSelectSession(session)">
                    <view class="session-item-copy">
                      <text class="session-item-title">{{ session.title || '未命名对话' }}</text>
                      <text v-if="session.preview" class="session-item-preview">{{ session.preview }}</text>
                    </view>
                    <text v-if="session.updated_at" class="session-item-time">{{ _formatSessionTime(session.updated_at) }}</text>
                  </view>
                  <view
                    class="session-item-action"
                    :class="String(session.session_id || '') === String(sessionActionBusyId || '') ? 'session-item-action-busy' : ''"
                    @tap.stop="handleSessionActions(session)"
                  >
                    <text class="session-item-action-mark">...</text>
                  </view>
                </view>
              </view>
            </view>
          </scroll-view>

          <view class="sidebar-footer">
            <view class="sidebar-footer-divider"></view>
            <view class="sidebar-logout" @tap="handleLogout">
              <text class="sidebar-logout-label">退出登录</text>
            </view>
          </view>
        </view>
      </view>
    </view>

    <!-- Phone Authorization Banner -->
    <view class="banner" v-if="!hasPhone">
      <button 
        class="phone-btn" 
        open-type="getPhoneNumber" 
        @getphonenumber="onGetPhoneNumber"
      >
        授权小程序取得手机号码，以便我们联系你
      </button>
    </view>

    <scroll-view class="scroll" scroll-y="true" :scroll-into-view="lastId" lower-threshold="50">
      <view class="scroll-shell">
        <message-list
          :messages="messages"
          :assistant-action-state="assistantActionState"
          :latest-assistant-id="latestAssistantMessageId"
          :show-latest-actions="Boolean(latestAssistantMessageId) && !sending"
          @assistant-action="onAssistantAction"
          @quick-reply="onSendQuickReply"
        />
      </view>
    </scroll-view>

    <view class="input-wrap">
      <input-bar
        :sending="sending"
        @send-text="onSendText"
        @send-audio="onSendAudio"
        @send-media="onSendMedia"
        @send-form="onSendForm"
      />
    </view>
  </view>
</template>

<script>
import MessageList from '../../components/MessageList.vue'
import InputBar from '../../components/InputBar.vue'
import { buildAuthorizedAudioUrl, createSession, deleteChatSession, getPhone, listChatSessions, prepareAudioSourceForPlayback, renameChatSession, streamChatReply, streamRegenerateReply, supportsDirectAudioPlayback, synthesizeSpeech, synthesizeSpeechStream, transcribeAudio, uploadFileOnly } from '../../utils/api.js'
import { resolveChatHeaderMetrics } from '../../utils/chatHeaderMetrics.js'

export default {
  components: { MessageList, InputBar },
  data() {
    return {
      messages: [],
      headerTopInsetExtraPx: -48,
      headerHeightExtraPx: 4,
      headerContentHeight: 44,
      headerTopInset: 0,
      menuOpen: false,
      menuPanelTop: 56,
      sessionSummaries: [],
      sessionsLoading: false,
      sessionsError: '',
      sessionActionBusyId: '',
      sending: false,
      lastId: '',
      currentSessionId: '',
      pendingMediaFileId: '',
      pendingMediaPreviewPath: '',
      pendingMediaType: '',
      hasPhone: false,
      speechAudioContext: null,
      speechActiveMessageId: '',
      speechLoadingMessageId: '',
      speechUrlMap: {}
    }
  },
  computed: {
    assistantActionState() {
      return {
        activeMessageId: this.speechActiveMessageId,
        loadingMessageId: this.speechLoadingMessageId
      }
    },
    chatHeaderSpacerStyle() {
      return `height:${this.headerTopInset}px;`
    },
    chatHeaderStyle() {
      return `height:${this.headerContentHeight}px;`
    },
    latestAssistantMessageId() {
      for (let index = this.messages.length - 1; index >= 0; index -= 1) {
        const message = this.messages[index]
        if (message && message.role === 'assistant' && message.type === 'text') {
          return String(message.id || '')
        }
      }
      return ''
    },
    sidebarDrawerStyle() {
      return `padding-top:${Math.max(Number(this.menuPanelTop) || 0, 16)}px;`
    }
  },
  async onLoad() {
    this._applyHeaderMetrics()
    this.$nextTick(() => {
      this._logHeaderLayout('onLoad')
    })
    this.hasPhone = uni.getStorageSync('has_phone') || false
    await this._startNewSession()
  },
  onReady() {
    this._logHeaderLayout('onReady')
  },
  onUnload() {
    this.menuOpen = false
    this._destroySpeechContext()
  },
  methods: {
    _applyHeaderMetrics() {
      const metrics = this._resolveHeaderMetrics()
      this.headerTopInset = metrics.headerTopInset
      this.headerContentHeight = metrics.headerContentHeight
      this.menuPanelTop = metrics.menuPanelTop
      console.log('chat header metrics', metrics)
    },
    _logHeaderLayout(stage = 'unknown') {
      try {
        const query = uni.createSelectorQuery().in(this)
        query.select('.chat-header-spacer').boundingClientRect()
        query.select('.chat-header').boundingClientRect()
        query.exec((rects) => {
          const spacerRect = rects && rects[0] ? rects[0] : null
          const headerRect = rects && rects[1] ? rects[1] : null
          console.log('chat header layout', {
            stage,
            headerTopInset: this.headerTopInset,
            headerContentHeight: this.headerContentHeight,
            menuPanelTop: this.menuPanelTop,
            spacerRect,
            headerRect
          })
        })
      } catch (error) {
        console.log('chat header layout log failed', { stage, error })
      }
    },
    _resolveHeaderMetrics() {
      let screenTop = 0
      let statusBarHeight = 0
      let safeAreaInsetTop = 0
      let safeAreaTop = 0
      let menuButton = null

      try {
        const info = typeof uni.getWindowInfo === 'function'
          ? uni.getWindowInfo()
          : uni.getSystemInfoSync()
        screenTop = Number((info && info.screenTop) || 0) || 0
        statusBarHeight = Number((info && info.statusBarHeight) || 0) || 0
        safeAreaInsetTop = Number((info && info.safeAreaInsets && info.safeAreaInsets.top) || 0) || 0
        safeAreaTop = Number((info && info.safeArea && info.safeArea.top) || 0) || 0
      } catch (error) {
        statusBarHeight = 0
      }

      try {
        menuButton = typeof wx !== 'undefined' && typeof wx.getMenuButtonBoundingClientRect === 'function'
          ? wx.getMenuButtonBoundingClientRect()
          : null
      } catch (error) {
      }

      return resolveChatHeaderMetrics({
        screenTop,
        statusBarHeight,
        safeAreaInsetTop,
        safeAreaTop,
        menuButton,
        headerTopInsetExtraPx: this.headerTopInsetExtraPx,
        headerHeightExtraPx: this.headerHeightExtraPx
      })
    },
    toggleMenu() {
      const nextState = !this.menuOpen
      this.menuOpen = nextState
      if (nextState) {
        this._refreshSessionSummaries({ silent: true })
      }
    },
    closeMenu() {
      this.menuOpen = false
    },
    noop() {
    },
    handleLogout() {
      this.closeMenu()
      uni.showModal({
        title: '退出登录',
        content: '确认退出当前账号？',
        success: ({ confirm }) => {
          if (!confirm) {
            return
          }
          this._performLogout()
        }
      })
    },
    _performLogout() {
      this.menuOpen = false
      this.messages = []
      this.lastId = ''
      this.currentSessionId = ''
      this.hasPhone = false
      this.sessionSummaries = []
      this.sessionsError = ''
      this._resetSessionUiState()

      const storageKeys = [
        'auth_token',
        'user_id',
        'external_id',
        'external_user_id',
        'nickname',
        'avatar',
        'has_phone'
      ]
      storageKeys.forEach((key) => {
        uni.removeStorageSync(key)
      })

      uni.reLaunch({ url: '/pages/index/index?loggedOut=1' })
    },
    async onGetPhoneNumber(e) {
      if (e.detail.errMsg !== 'getPhoneNumber:ok') {
        uni.showToast({ title: '授权失败', icon: 'none' })
        return
      }
      try {
        await getPhone({ code: e.detail.code })
        this.hasPhone = true
        uni.setStorageSync('has_phone', true)
        uni.showToast({ title: '已取得手机号', icon: 'success' })
      } catch (err) {
        uni.showToast({ title: '获取手机号失败', icon: 'none' })
      }
    },
    _resetSessionUiState() {
      this.pendingMediaFileId = ''
      this.pendingMediaPreviewPath = ''
      this.pendingMediaType = ''
      this.speechActiveMessageId = ''
      this.speechLoadingMessageId = ''
      this.speechUrlMap = {}
      this._destroySpeechContext()
    },

    _normalizeHistoryAssetUrl(value) {
      const normalizedValue = String(value || '').trim()
      if (!normalizedValue) {
        return ''
      }
      return buildAuthorizedAudioUrl(normalizedValue) || normalizedValue
    },

    _extractTriageAttachments(meta) {
      const source = meta && Array.isArray(meta.attachments) ? meta.attachments : []
      const seen = {}
      const result = []
      source.forEach((item) => {
        if (!item || item.type !== 'image') {
          return
        }
        const rawUrl = String(item.url || item.path || '').trim()
        if (!rawUrl || seen[rawUrl]) {
          return
        }
        seen[rawUrl] = true
        result.push({
          url: this._normalizeHistoryAssetUrl(rawUrl),
          caption: String(item.caption || '').trim()
        })
      })
      return result
    },

    _extractQuickReplies(meta) {
      const source = meta && Array.isArray(meta.quick_replies) ? meta.quick_replies : []
      return source
        .map((item) => ({
          label_zh: String((item && item.label_zh) || (item && item.value) || '').trim(),
          value: String((item && item.value) || '').trim()
        }))
        .filter((item) => item.value)
    },

    _setMessageMeta(messageId, meta) {
      const index = this.messages.findIndex((msg) => msg.id === messageId)
      if (index === -1) {
        return
      }
      this.messages[index] = {
        ...this.messages[index],
        attachments: this._extractTriageAttachments(meta),
        quickReplies: this._extractQuickReplies(meta)
      }
      this.lastId = 'm_' + index
    },

    _setSessionSummaries(sessions) {
      this.sessionSummaries = Array.isArray(sessions)
        ? sessions
          .map((session) => ({
            session_id: String((session && session.session_id) || ''),
            title: String((session && session.title) || '').trim(),
            preview: String((session && session.preview) || '').trim(),
            updated_at: String((session && session.updated_at) || '').trim(),
            created_at: String((session && session.created_at) || '').trim()
          }))
          .filter((session) => session.session_id)
        : []
    },

    _normalizeSessionTitle(value) {
      return String(value || '').replace(/\s+/g, ' ').trim()
    },

    _showConfirmModal(options) {
      return new Promise((resolve, reject) => {
        uni.showModal({
          ...options,
          success: resolve,
          fail: reject
        })
      })
    },

    _showEditableModal(options) {
      return new Promise((resolve, reject) => {
        uni.showModal({
          ...options,
          editable: true,
          success: resolve,
          fail: reject
        })
      })
    },

    _applySessionBootstrap(payload) {
      this.currentSessionId = payload && payload.session_id ? String(payload.session_id) : ''

      if (payload && payload.external_user_id) {
        uni.setStorageSync('external_user_id', payload.external_user_id)
      }

      if (payload && Array.isArray(payload.sessions)) {
        this._setSessionSummaries(payload.sessions)
      }

      const restored = this._restoreHistoryMessages(payload && payload.messages)
      if (!restored && payload && payload.greeting) {
        this.messages = []
        this._appendMessage({
          id: 'greeting',
          role: 'assistant',
          type: 'text',
          content: payload.greeting
        })
      }

      this._resetSessionUiState()
    },

    async _refreshSessionSummaries({ silent = false } = {}) {
      if (this.sessionsLoading) {
        return
      }

      this.sessionsLoading = true
      if (!silent) {
        this.sessionsError = ''
      }

      try {
        const result = await listChatSessions()
        this._setSessionSummaries(result && result.sessions)
        this.sessionsError = ''
      } catch (error) {
        this.sessionsError = '历史对话加载失败'
        if (!silent) {
          uni.showToast({ title: this.sessionsError, icon: 'none' })
        }
      } finally {
        this.sessionsLoading = false
      }
    },

    _formatSessionTime(value) {
      const normalizedValue = String(value || '').trim()
      if (!normalizedValue) {
        return ''
      }

      const date = new Date(normalizedValue)
      if (Number.isNaN(date.getTime())) {
        return normalizedValue.slice(0, 10)
      }

      const now = new Date()
      const pad = (number) => String(number).padStart(2, '0')
      const sameDay = date.getFullYear() === now.getFullYear() &&
        date.getMonth() === now.getMonth() &&
        date.getDate() === now.getDate()

      if (sameDay) {
        return `${pad(date.getHours())}:${pad(date.getMinutes())}`
      }

      if (date.getFullYear() === now.getFullYear()) {
        return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
      }

      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    },

    _normalizeHistoryMessage(message, index) {
      if (!message || typeof message !== 'object') {
        return null
      }

      const role = message.role === 'user' ? 'user' : 'assistant'
      const type = String(message.type || 'text')
      const id = String(message.id || `${role}_${index}`)

      if (type === 'form') {
        const content = message.content && typeof message.content === 'object' ? message.content : {}
        return {
          id,
          role,
          type: 'form',
          content: {
            category: String(content.category || ''),
            detail: String(content.detail || '')
          }
        }
      }

      if (type === 'image') {
        const content = message.content && typeof message.content === 'object' ? message.content : {}
        const filePath = this._normalizeHistoryAssetUrl(content.filePath || content.previewPath || content.url)
        if (!filePath) {
          return {
            id,
            role,
            type: 'text',
            content: '图片消息'
          }
        }
        return {
          id,
          role,
          type: 'image',
          content: {
            ...content,
            filePath,
            previewPath: filePath
          }
        }
      }

      if (type === 'audio') {
        const content = message.content && typeof message.content === 'object' ? message.content : {}
        const filePath = this._normalizeHistoryAssetUrl(content.filePath || content.audioUrl)
        return {
          id,
          role,
          type: 'audio',
          content: {
            ...content,
            ...(filePath ? { filePath } : {}),
            playable: filePath ? content.playable !== false : false,
            duration: Number(content.duration || 0) || 0,
            transcription: String(content.transcription || content.transcript || '')
          }
        }
      }

      const meta = message.meta && typeof message.meta === 'object' ? message.meta : null
      const textMessage = {
        id,
        role,
        type: 'text',
        content: String(
          type === 'text'
            ? (message.content || '')
            : ((message.content && typeof message.content === 'object' && message.content.text) || message.content || '')
        )
      }

      if (role === 'assistant' && meta) {
        const attachments = this._extractTriageAttachments(meta)
        const quickReplies = this._extractQuickReplies(meta)
        if (attachments.length) {
          textMessage.attachments = attachments
        }
        if (quickReplies.length) {
          textMessage.quickReplies = quickReplies
        }
      }

      return textMessage
    },

    _restoreHistoryMessages(messages) {
      const restoredMessages = Array.isArray(messages)
        ? messages
          .map((message, index) => this._normalizeHistoryMessage(message, index))
          .filter(Boolean)
        : []

      this.messages = restoredMessages
      this.lastId = restoredMessages.length ? 'm_' + (restoredMessages.length - 1) : ''
      return restoredMessages.length > 0
    },

    async _startNewSession({ forceNew = false, sessionId = '', closeMenu = false } = {}) {
      try {
        const sessionPayload = await createSession({ forceNew, sessionId })
        this._applySessionBootstrap(sessionPayload)
        this.sessionsError = ''
        if (closeMenu) {
          this.closeMenu()
        }
      } catch (e) {
        const title = sessionId ? '切换会话失败' : '创建会话失败'
        uni.showToast({ title, icon: 'none' })
      }
    },

    async handleCreateNewSession() {
      if (this.sending) {
        return
      }
      await this._startNewSession({ forceNew: true, closeMenu: true })
    },

    async handleSelectSession(session) {
      const sessionId = String((session && session.session_id) || '').trim()
      if (!sessionId || this.sending || this.sessionActionBusyId) {
        return
      }
      if (sessionId === String(this.currentSessionId || '')) {
        this.closeMenu()
        return
      }
      await this._startNewSession({ sessionId, closeMenu: true })
    },

    handleSessionActions(session) {
      const sessionId = String((session && session.session_id) || '').trim()
      if (!sessionId || this.sending || this.sessionActionBusyId) {
        return
      }

      uni.showActionSheet({
        itemList: ['重命名', '删除'],
        success: ({ tapIndex }) => {
          if (tapIndex === 0) {
            this.handleRenameSession(session)
            return
          }
          if (tapIndex === 1) {
            this.handleDeleteSession(session)
          }
        }
      })
    },

    async handleRenameSession(session) {
      const sessionId = String((session && session.session_id) || '').trim()
      if (!sessionId || this.sending || this.sessionActionBusyId) {
        return
      }

      let modalResult
      try {
        modalResult = await this._showEditableModal({
          title: '重命名会话',
          placeholderText: '输入新的会话名称',
          content: String((session && session.title) || '')
        })
      } catch (error) {
        uni.showToast({ title: '无法打开重命名弹窗', icon: 'none' })
        return
      }

      if (!modalResult || !modalResult.confirm) {
        return
      }

      this.sessionActionBusyId = sessionId
      try {
        const result = await renameChatSession(sessionId, this._normalizeSessionTitle(modalResult.content), {
          currentSessionId: this.currentSessionId
        })
        if (result && Array.isArray(result.sessions)) {
          this._setSessionSummaries(result.sessions)
        }
        uni.showToast({
          title: this._normalizeSessionTitle(modalResult.content) ? '会话已重命名' : '已恢复默认标题',
          icon: 'none'
        })
      } catch (error) {
        uni.showToast({ title: '重命名会话失败', icon: 'none' })
      } finally {
        this.sessionActionBusyId = ''
      }
    },

    async handleDeleteSession(session) {
      const sessionId = String((session && session.session_id) || '').trim()
      if (!sessionId || this.sending || this.sessionActionBusyId) {
        return
      }

      let modalResult
      try {
        modalResult = await this._showConfirmModal({
          title: '删除会话',
          content: '确认删除这个会话吗？',
          confirmColor: '#dc2626'
        })
      } catch (error) {
        uni.showToast({ title: '无法打开删除确认', icon: 'none' })
        return
      }

      if (!modalResult || !modalResult.confirm) {
        return
      }

      this.sessionActionBusyId = sessionId
      try {
        const result = await deleteChatSession(sessionId, {
          currentSessionId: this.currentSessionId
        })

        if (result && result.current_session_deleted) {
          if (result.bootstrap) {
            this._applySessionBootstrap(result.bootstrap)
            this.closeMenu()
          } else {
            await this._startNewSession({ forceNew: true, closeMenu: true })
          }
        } else if (result && Array.isArray(result.sessions)) {
          this._setSessionSummaries(result.sessions)
        }

        uni.showToast({ title: '会话已删除', icon: 'none' })
      } catch (error) {
        uni.showToast({ title: '删除会话失败', icon: 'none' })
      } finally {
        this.sessionActionBusyId = ''
      }
    },

    _nextId() {
      return Date.now() + '_' + Math.random().toString(16).slice(2)
    },

    _appendMessage(msg) {
      this.messages.push(msg)
      this.lastId = 'm_' + (this.messages.length - 1)
    },

    _setMessageContent(messageId, content) {
      const index = this.messages.findIndex((msg) => msg.id === messageId)
      if (index === -1) {
        return
      }

      this.messages[index] = {
        ...this.messages[index],
        content
      }
      this.lastId = 'm_' + index
    },

    _setAudioTranscript(messageId, transcription) {
      const index = this.messages.findIndex((msg) => msg.id === messageId)
      if (index === -1) {
        return
      }

      const currentMessage = this.messages[index] || {}
      const currentContent = currentMessage && typeof currentMessage.content === 'object' && currentMessage.content
        ? currentMessage.content
        : {}

      this.messages[index] = {
        ...currentMessage,
        content: {
          ...currentContent,
          transcription
        }
      }
      this.lastId = 'm_' + index
    },

    _getMessageContent(messageId) {
      const message = this.messages.find((item) => item.id === messageId)
      return message ? String(message.content || '') : ''
    },

    _getMessageById(messageId) {
      const normalizedId = String(messageId || '')
      return this.messages.find((item) => String(item.id || '') === normalizedId) || null
    },

    _assistantReplyText(messageOrId) {
      const message = messageOrId && typeof messageOrId === 'object'
        ? messageOrId
        : this._getMessageById(messageOrId)

      if (!message || message.role !== 'assistant' || message.type !== 'text') {
        return ''
      }

      return String(message.content || '').trim()
    },

    _removeMessage(messageId) {
      const index = this.messages.findIndex((msg) => msg.id === messageId)
      if (index === -1) {
        return
      }

      this.messages.splice(index, 1)
      this.lastId = this.messages.length ? 'm_' + (this.messages.length - 1) : ''
    },

    _cachedSpeechUrl(messageId) {
      const normalizedId = String(messageId || '').trim()
      return normalizedId ? String(this.speechUrlMap[normalizedId] || '').trim() : ''
    },

    _cacheSpeechUrl(messageId, audioUrl) {
      const normalizedId = String(messageId || '').trim()
      const normalizedUrl = String(audioUrl || '').trim()
      if (!normalizedId || !normalizedUrl) {
        return
      }

      this.speechUrlMap[normalizedId] = normalizedUrl
    },

    _clearSpeechCache(messageId) {
      const normalizedId = String(messageId || '').trim()
      if (!normalizedId) {
        return
      }

      if (this.speechActiveMessageId === normalizedId) {
        this._stopSpeechPlayback(normalizedId)
      }
      if (this.speechLoadingMessageId === normalizedId) {
        this.speechLoadingMessageId = ''
      }
      if (Object.prototype.hasOwnProperty.call(this.speechUrlMap, normalizedId)) {
        delete this.speechUrlMap[normalizedId]
      }
    },

    _destroySpeechContext() {
      const audioContext = this.speechAudioContext
      this.speechAudioContext = null
      if (!audioContext) {
        return
      }

      try {
        audioContext.stop()
      } catch (error) {
      }

      try {
        audioContext.destroy()
      } catch (error) {
      }
    },

    _createSpeechContext(messageId) {
      if (typeof uni.createInnerAudioContext !== 'function') {
        throw new Error('当前环境不支持语音播放')
      }

      const normalizedId = String(messageId || '').trim()
      const audioContext = uni.createInnerAudioContext()
      audioContext.autoplay = false
      if ('obeyMuteSwitch' in audioContext) {
        audioContext.obeyMuteSwitch = false
      }

      const cleanup = ({ showError = false } = {}) => {
        if (this.speechActiveMessageId === normalizedId) {
          this.speechActiveMessageId = ''
        }
        if (this.speechLoadingMessageId === normalizedId) {
          this.speechLoadingMessageId = ''
        }
        if (this.speechAudioContext === audioContext) {
          this.speechAudioContext = null
        }
        try {
          audioContext.destroy()
        } catch (error) {
        }
        console.error('speech playback cleanup', {
          messageId: normalizedId,
          showError
        })
        if (showError) {
          uni.showToast({ title: '语音播放失败', icon: 'none' })
        }
      }

      audioContext.onPlay(() => {
        if (this.speechLoadingMessageId === normalizedId) {
          this.speechLoadingMessageId = ''
        }
        this.speechActiveMessageId = normalizedId
      })
      audioContext.onEnded(() => cleanup())
      audioContext.onStop(() => cleanup())
      audioContext.onError((error) => {
        console.error('speech playback error', {
          messageId: normalizedId,
          error
        })
        cleanup({ showError: true })
      })

      this.speechAudioContext = audioContext
      return audioContext
    },

    _stopSpeechPlayback(messageId = '') {
      const normalizedId = String(messageId || '').trim()
      if (normalizedId && normalizedId !== String(this.speechActiveMessageId || '')) {
        return
      }

      this.speechActiveMessageId = ''
      this._destroySpeechContext()
    },

    _startSpeechPlayback({ messageId, audioUrl }) {
      const normalizedId = String(messageId || '').trim()
      const normalizedUrl = String(audioUrl || '').trim()
      if (!normalizedId || !normalizedUrl) {
        return false
      }

      this._destroySpeechContext()
      const audioContext = this._createSpeechContext(normalizedId)
      this.speechActiveMessageId = normalizedId
      this.speechLoadingMessageId = ''

      try {
        audioContext.src = normalizedUrl
        audioContext.play()
        return true
      } catch (error) {
        this.speechActiveMessageId = ''
        this._destroySpeechContext()
        throw error
      }
    },

    async _fetchSpeechAudioUrl(messageId, text) {
      const normalizedId = String(messageId || '').trim()
      if (!normalizedId) {
        return ''
      }

      this.speechLoadingMessageId = normalizedId
      try {
        if (supportsDirectAudioPlayback('/api/speak/stream')) {
          try {
            const streamResult = await synthesizeSpeechStream({
              text,
              messageId: normalizedId
            })
            const streamAudioUrl = buildAuthorizedAudioUrl(streamResult && streamResult.stream_url)
            if (streamAudioUrl) {
              this._cacheSpeechUrl(normalizedId, streamAudioUrl)
              return streamAudioUrl
            }
          } catch (error) {
            console.warn('Speech stream fallback to buffered download', error)
          }
        }

        const result = await synthesizeSpeech({
          text,
          messageId: normalizedId
        })
        const remoteAudioUrl = String(result && result.audio_url ? result.audio_url : '').trim()
        if (!remoteAudioUrl) {
          throw new Error('语音合成失败')
        }
        const playableAudioPath = await prepareAudioSourceForPlayback({
          audioUrl: remoteAudioUrl,
          cacheKey: normalizedId
        })
        const normalizedPlayablePath = String(playableAudioPath || remoteAudioUrl).trim()
        if (!normalizedPlayablePath) {
          throw new Error('音频准备失败')
        }
        this._cacheSpeechUrl(normalizedId, normalizedPlayablePath)
        return normalizedPlayablePath
      } finally {
        if (this.speechLoadingMessageId === normalizedId) {
          this.speechLoadingMessageId = ''
        }
      }
    },

    async _playAssistantSpeech({ messageId, message = null }) {
      const normalizedId = String(messageId || '').trim()
      if (!normalizedId) {
        return false
      }
      if (this.speechLoadingMessageId === normalizedId) {
        return false
      }
      if (this.speechActiveMessageId === normalizedId) {
        this._stopSpeechPlayback(normalizedId)
        return true
      }

      const replyText = this._assistantReplyText(message || normalizedId)
      if (!replyText) {
        uni.showToast({ title: '暂无可朗读内容', icon: 'none' })
        return false
      }

      if (this.speechActiveMessageId) {
        this._stopSpeechPlayback()
      }

      try {
        let audioUrl = this._cachedSpeechUrl(normalizedId)
        if (!audioUrl) {
          audioUrl = await this._fetchSpeechAudioUrl(normalizedId, replyText)
        }
        if (!audioUrl) {
          throw new Error('语音合成失败')
        }
        return this._startSpeechPlayback({ messageId: normalizedId, audioUrl })
      } catch (error) {
        const title = error && error.message ? String(error.message) : '语音合成失败'
        uni.showToast({ title: title.slice(0, 20), icon: 'none' })
        return false
      }
    },

    async _regenerateAssistantMessage(messageId) {
      const normalizedId = String(messageId || '').trim()
      if (!normalizedId || !this.currentSessionId) {
        uni.showToast({ title: '当前回复无法重答', icon: 'none' })
        return false
      }
      if (normalizedId !== this.latestAssistantMessageId) {
        uni.showToast({ title: '仅支持重答最新回复', icon: 'none' })
        return false
      }

      const message = this._getMessageById(normalizedId)
      if (!message || message.role !== 'assistant' || message.type !== 'text') {
        uni.showToast({ title: '当前回复无法重答', icon: 'none' })
        return false
      }

      const previousReplyText = this._assistantReplyText(message)
      this._clearSpeechCache(normalizedId)
      this.sending = true
      let finalPayload = null

      try {
        finalPayload = await streamRegenerateReply({
          sessionId: this.currentSessionId,
          messageId: normalizedId,
          onStart: (payload) => {
            if (payload && payload.session_id) {
              this.currentSessionId = payload.session_id
            }
            this._setMessageContent(normalizedId, '')
          },
          onDelta: (payload) => {
            const deltaText = payload && payload.text != null ? String(payload.text) : ''
            if (!deltaText) {
              return
            }
            this._setMessageContent(
              normalizedId,
              this._getMessageContent(normalizedId) + deltaText
            )
          },
          onFinal: (payload) => {
            finalPayload = payload || finalPayload
            if (payload && payload.session_id) {
              this.currentSessionId = payload.session_id
            }
            const finalText = this._extractReplyText(payload) || this._getMessageContent(normalizedId)
            if (finalText) {
              this._setMessageContent(normalizedId, finalText)
            }
          }
        })

        const replyText = this._extractReplyText(finalPayload) || this._getMessageContent(normalizedId)
        if (!replyText) {
          throw new Error('重答失败')
        }

        this._clearSpeechCache(normalizedId)
        uni.showToast({
          title: replyText.trim() === previousReplyText.trim() ? '已重答，结果相近' : '已重新生成',
          icon: 'none'
        })
        return true
      } catch (error) {
        this._setMessageContent(normalizedId, previousReplyText)
        this._clearSpeechCache(normalizedId)
        const title = error && error.message ? String(error.message) : '重答失败'
        uni.showToast({ title: title.slice(0, 20), icon: 'none' })
        return false
      } finally {
        this.sending = false
      }
    },

    _clearPendingMedia() {
      this.pendingMediaFileId = ''
      this.pendingMediaPreviewPath = ''
      this.pendingMediaType = ''
    },

    _extractReplyText(payload) {
      if (!payload || typeof payload !== 'object') {
        return ''
      }
      if (payload.reply != null) {
        return String(payload.reply)
      }
      if (payload.reply_text != null) {
        return String(payload.reply_text)
      }
      if (payload.text != null) {
        return String(payload.text)
      }
      return ''
    },

    async _transcribeVoiceMessage(filePath) {
      try {
        const result = await transcribeAudio({ filePath, fileName: 'voice.aac' })
        return result && result.text ? String(result.text).trim() : ''
      } catch (error) {
        const statusCode = Number(error && error.statusCode)
        if (statusCode >= 400 && statusCode < 500) {
          throw error
        }
        return ''
      }
    },

    async _streamReply({ type, content, autoSpeakReply = false }) {
      if (!uni.getStorageSync('external_user_id')) {
        uni.showToast({ title: '请先设置 external_user_id', icon: 'none' })
        return null
      }

      const assistantMessageId = this._nextId()
      this._appendMessage({
        id: assistantMessageId,
        role: 'assistant',
        type: 'text',
        content: ''
      })

      this.sending = true
      let finalPayload = null

      try {
        finalPayload = await streamChatReply({
          type,
          content,
          sessionId: this.currentSessionId,
          onStart: (payload) => {
            if (payload && payload.session_id) {
              this.currentSessionId = payload.session_id
            }
          },
          onDelta: (payload) => {
            const deltaText = payload && payload.text != null ? String(payload.text) : ''
            if (!deltaText) {
              return
            }
            this._setMessageContent(
              assistantMessageId,
              this._getMessageContent(assistantMessageId) + deltaText
            )
          },
          onFinal: (payload) => {
            finalPayload = payload || finalPayload
            if (payload && payload.session_id) {
              this.currentSessionId = payload.session_id
            }
            const finalText = this._extractReplyText(payload) || this._getMessageContent(assistantMessageId)
            if (finalText) {
              this._setMessageContent(assistantMessageId, finalText)
            }
            this._setMessageMeta(assistantMessageId, payload && payload.meta)
          }
        })
      } catch (error) {
        if (!this._getMessageContent(assistantMessageId)) {
          this._removeMessage(assistantMessageId)
        }
        uni.showToast({ title: '发送失败', icon: 'none' })
        finalPayload = null
      } finally {
        this.sending = false
      }

      if (finalPayload && autoSpeakReply) {
        const replyText = this._assistantReplyText(assistantMessageId)
        if (replyText) {
          await this._playAssistantSpeech({ messageId: assistantMessageId })
        }
      }

      return finalPayload
    },

    async onSendText(text) {
      const userMsg = {
        id: this._nextId(),
        role: 'user',
        type: 'text',
        content: text
      }
      this._appendMessage(userMsg)

      if (this.pendingMediaFileId) {
        const payload = {
          text,
          imageFileIds: undefined,
          videoFileIds: undefined
        }

        if (this.pendingMediaType === 'video') {
          payload.videoFileIds = [this.pendingMediaFileId]
        } else {
          payload.imageFileIds = [this.pendingMediaFileId]
        }

        const reply = await this._callMixedBackend(payload)
        if (reply) {
          this._clearPendingMedia()
        }
        return
      }

      await this._callBackend({ type: 'text', content: { text } })
    },

    async onSendQuickReply(reply) {
      if (this.sending) {
        return
      }
      const value = String((reply && reply.value) || '').trim()
      if (!value) {
        return
      }
      const label = String((reply && reply.label_zh) || value).trim()
      this._appendMessage({
        id: this._nextId(),
        role: 'user',
        type: 'text',
        content: label
      })
      await this._callBackend({ type: 'text', content: { text: label, quick_value: value } })
    },

    async onSendForm(payload) {
      const userMsg = {
        id: this._nextId(),
        role: 'user',
        type: 'form',
        content: payload
      }
      this._appendMessage(userMsg)
      await this._callBackend({ type: 'form', content: payload })
    },

    async onAssistantAction({ action, messageId, message }) {
      if (action === 'speak') {
        await this._playAssistantSpeech({ messageId, message })
        return
      }
      if (action === 'regenerate') {
        await this._regenerateAssistantMessage(messageId)
        return
      }

      const labels = {
        like: '反馈功能开发中',
        dislike: '反馈功能开发中'
      }
      uni.showToast({ title: labels[action] || '功能开发中', icon: 'none' })
    },

    async onSendAudio({ filePath, duration }) {
      const userMsg = {
        id: this._nextId(),
        role: 'user',
        type: 'audio',
        content: { filePath, duration, playable: false }
      }
      this._appendMessage(userMsg)
      await this._sendAudio(filePath, userMsg.id)
    },

    async onSendMedia(payload) {
      const filePath = typeof payload === 'string' ? payload : payload.filePath
      const fileType = typeof payload === 'string' ? 'image' : (payload.fileType || 'image')
      const previewPath = typeof payload === 'string'
        ? payload
        : (payload.thumbPath || payload.previewPath || payload.filePath)
      const textDraft = typeof payload === 'string' ? '' : (payload.textDraft || '')

      const userMsg = {
        id: this._nextId(),
        role: 'user',
        type: fileType,
        content: {
          filePath,
          previewPath
        }
      }
      this._appendMessage(userMsg)

      await this._uploadMediaAsPending(filePath, fileType, previewPath)
      if (!this.pendingMediaFileId) {
        return
      }

      if (textDraft && textDraft.trim()) {
        const mediaPayload = {
          text: textDraft.trim(),
          imageFileIds: undefined,
          videoFileIds: undefined
        }

        if (fileType === 'video') {
          mediaPayload.videoFileIds = [this.pendingMediaFileId]
        } else {
          mediaPayload.imageFileIds = [this.pendingMediaFileId]
        }

        const reply = await this._callMixedBackend(mediaPayload)
        if (reply) {
          this._clearPendingMedia()
        }
      } else {
        const hint = fileType === 'video'
          ? '视频已附加，请发送文字或语音一起提交'
          : '图片已附加，请发送文字或语音一起提交'
        uni.showToast({ title: hint, icon: 'none' })
      }
    },

    async _callBackend({ type, content, autoSpeakReply = false }) {
      return this._streamReply({ type, content, autoSpeakReply })
    },

    async _uploadMediaAsPending(filePath, fileType = 'image', previewPath = '') {
      if (!uni.getStorageSync('external_user_id')) {
        uni.showToast({ title: '请先设置 external_user_id', icon: 'none' })
        return
      }

      this.sending = true
      try {
        const uploaded = await uploadFileOnly({ type: fileType, filePath })
        this.pendingMediaFileId = uploaded.file_id
        this.pendingMediaPreviewPath = previewPath || filePath
        this.pendingMediaType = fileType
      } catch (e) {
        uni.showToast({
          title: `${fileType === 'video' ? '视频' : '图片'}上传失败`,
          icon: 'none'
        })
      } finally {
        this.sending = false
      }
    },

    async _sendAudio(filePath, messageId = '') {
      if (!uni.getStorageSync('external_user_id')) {
        uni.showToast({ title: '请先设置 external_user_id', icon: 'none' })
        return
      }

      this.sending = true
      try {
        const transcript = await this._transcribeVoiceMessage(filePath)
        if (transcript) {
          if (messageId) {
            this._setAudioTranscript(messageId, transcript)
          }

          if (this.pendingMediaFileId) {
            const payload = {
              text: transcript,
              imageFileIds: undefined,
              videoFileIds: undefined
            }

            if (this.pendingMediaType === 'video') {
              payload.videoFileIds = [this.pendingMediaFileId]
            } else {
              payload.imageFileIds = [this.pendingMediaFileId]
            }

            const reply = await this._callMixedBackend({
              ...payload,
              autoSpeakReply: true
            })
            if (reply) {
              this._clearPendingMedia()
            }
          } else {
            await this._callBackend({
              type: 'text',
              content: { text: transcript },
              autoSpeakReply: true
            })
          }
          return
        }

        const uploaded = await uploadFileOnly({ type: 'audio', filePath })

        if (this.pendingMediaFileId) {
          const payload = {
            audioFileId: uploaded.file_id,
            sessionId: this.currentSessionId,
            imageFileIds: undefined,
            videoFileIds: undefined
          }

          if (this.pendingMediaType === 'video') {
            payload.videoFileIds = [this.pendingMediaFileId]
          } else {
            payload.imageFileIds = [this.pendingMediaFileId]
          }

          const reply = await this._callMixedBackend({
            ...payload,
            autoSpeakReply: true
          })
          if (reply) {
            this._clearPendingMedia()
          }
        } else {
          await this._callBackend({
            type: 'audio',
            content: { audio_file_id: uploaded.file_id },
            autoSpeakReply: true
          })
        }
      } catch (e) {
        const title = e && e.message ? String(e.message) : '语音发送失败'
        uni.showToast({ title: title.slice(0, 20), icon: 'none' })
      } finally {
        this.sending = false
      }
    },

    async _callMixedBackend({ text, imageFileIds, videoFileIds, audioFileId, autoSpeakReply = false }) {
      const content = {}
      if (text && text.trim()) content.text = text.trim()
      if (imageFileIds && imageFileIds.length) content.image_file_ids = imageFileIds
      if (videoFileIds && videoFileIds.length) content.video_file_ids = videoFileIds
      if (audioFileId) content.audio_file_id = audioFileId
      return this._streamReply({ type: 'mixed', content, autoSpeakReply })
    }
  }
}
</script>

<style scoped>
.page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #f7f8fb 0%, #eef3fb 42%, #edf2f8 100%);
  --assistant-bubble: rgba(255, 255, 255, 0.84);
  --user-bubble: #dcecff;
  --composer-surface: rgba(255, 255, 255, 0.95);
}
.chat-header-spacer {
  flex: 0 0 auto;
}
.chat-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
  padding: 0 20rpx 4rpx;
}
.chat-header-side {
  width: 76rpx;
  display: flex;
  align-items: center;
}
.chat-header-title {
  flex: 1;
  text-align: center;
  font-size: 30rpx;
  font-weight: 700;
  color: #0f172a;
}
.menu-trigger {
  width: 68rpx;
  height: 68rpx;
  padding: 0 18rpx;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 7rpx;
  border-radius: 22rpx;
  border: 1rpx solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 12rpx 30rpx rgba(15, 23, 42, 0.08);
}
.menu-trigger-line {
  width: 24rpx;
  height: 4rpx;
  border-radius: 999rpx;
  background: #0f172a;
  align-self: center;
}
.menu-trigger-line:nth-child(2) {
  width: 20rpx;
}
.sidebar-mask {
  position: fixed;
  inset: 0;
  z-index: 34;
  background: rgba(15, 23, 42, 0.22);
}
.sidebar-drawer {
  position: fixed;
  inset: 0 auto 0 0;
  width: 560rpx;
  max-width: 84vw;
  background: rgba(248, 250, 252, 0.98);
  box-shadow: 32rpx 0 72rpx rgba(15, 23, 42, 0.18);
  backdrop-filter: blur(18px);
  animation: sidebarSlideIn 0.18s ease-out;
}
.sidebar-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.sidebar-scroll {
  flex: 1;
  min-height: 0;
  padding: 20rpx 18rpx 16rpx;
  box-sizing: border-box;
}

.sidebar-section {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.sidebar-section + .sidebar-section {
  margin-top: 26rpx;
}

.sidebar-section-primary {
  margin-bottom: 10rpx;
}

.sidebar-action {
  min-height: 78rpx;
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 0 18rpx;
  border-radius: 24rpx;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 10rpx 24rpx rgba(15, 23, 42, 0.08);
}

.sidebar-action-icon {
  width: 44rpx;
  height: 44rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 14rpx;
  background: rgba(15, 23, 42, 0.05);
  color: #0f172a;
}

.sidebar-action-icon-mark {
  font-size: 32rpx;
  font-weight: 600;
  line-height: 1;
}

.sidebar-action-copy {
  flex: 1;
  min-width: 0;
}

.sidebar-action-title {
  display: block;
  font-size: 28rpx;
  font-weight: 600;
  color: #0f172a;
}

.sidebar-section-title {
  font-size: 22rpx;
  font-weight: 600;
  color: #64748b;
  padding: 0 8rpx;
}

.sidebar-history-list {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.sidebar-history-state {
  padding: 24rpx 10rpx;
  font-size: 24rpx;
  color: #64748b;
}

.sidebar-history-state-error {
  color: #dc2626;
}

.session-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
  padding: 18rpx 18rpx 20rpx;
  border-radius: 24rpx;
  background: transparent;
}

.session-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
}

.session-item-active {
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 10rpx 26rpx rgba(15, 23, 42, 0.08);
}

.session-item-copy {
  flex: 1;
  min-width: 0;
}

.session-item-title {
  display: block;
  font-size: 27rpx;
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-item-preview {
  display: block;
  margin-top: 8rpx;
  font-size: 23rpx;
  line-height: 1.45;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-item-time {
  flex: 0 0 auto;
  font-size: 22rpx;
  color: #94a3b8;
  line-height: 1.6;
}

.session-item-action {
  flex: 0 0 auto;
  min-width: 60rpx;
  min-height: 60rpx;
  margin-top: -2rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 18rpx;
  background: rgba(226, 232, 240, 0.55);
}

.session-item-active .session-item-action {
  background: rgba(226, 232, 240, 0.86);
}

.session-item-action-busy {
  opacity: 0.45;
}

.session-item-action-mark {
  font-size: 24rpx;
  line-height: 1;
  color: #475569;
  letter-spacing: 2rpx;
}

.sidebar-footer {
  padding: 16rpx 18rpx calc(20rpx + constant(safe-area-inset-bottom));
  padding: 16rpx 18rpx calc(20rpx + env(safe-area-inset-bottom));
}

.sidebar-footer-divider {
  height: 1rpx;
  margin-bottom: 16rpx;
  background: rgba(148, 163, 184, 0.18);
}

.sidebar-logout {
  min-height: 76rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 24rpx;
  background: rgba(255, 255, 255, 0.96);
  border: 1rpx solid rgba(248, 113, 113, 0.22);
  box-shadow: 0 10rpx 24rpx rgba(15, 23, 42, 0.06);
}

.sidebar-logout-label {
  font-size: 27rpx;
  font-weight: 600;
  color: #dc2626;
}
.banner {
  margin: 20rpx 20rpx 0;
  padding: 18rpx 22rpx;
  background: rgba(255, 247, 219, 0.88);
  border: 1rpx solid rgba(245, 158, 11, 0.2);
  border-radius: 28rpx;
  box-shadow: 0 12rpx 28rpx rgba(148, 163, 184, 0.1);
}
.phone-btn {
  font-size: 26rpx;
  color: #856404;
  background: rgba(255, 243, 205, 0.84);
  border: 1rpx solid rgba(245, 158, 11, 0.2);
  border-radius: 20rpx;
  line-height: 2.4;
}
.scroll {
  flex: 1;
  min-height: 0;
}

.scroll-shell {
  min-height: 100%;
  box-sizing: border-box;
  padding: 24rpx 20rpx 220rpx;
}

.input-wrap {
  padding: 8rpx 20rpx calc(18rpx + constant(safe-area-inset-bottom));
  padding: 8rpx 20rpx calc(18rpx + env(safe-area-inset-bottom));
  background: linear-gradient(180deg, rgba(237, 242, 248, 0) 0%, rgba(237, 242, 248, 0.74) 38%, rgba(237, 242, 248, 1) 100%);
}

@keyframes sidebarSlideIn {
  from {
    transform: translateX(-32rpx);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}
</style>