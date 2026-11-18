import { API_BASE_URL } from './config'

function getExternalUserId() {
  const id = uni.getStorageSync('external_user_id')
  return id || ''
}

function getAuthToken() {
  return uni.getStorageSync('auth_token') || ''
}

function authHeader() {
  const authToken = getAuthToken()
  const headers = {
    'Helpdesk-Client-Type': 'wechat-mini'
  }
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`
  }
  const externalUserId = getExternalUserId()
  if (externalUserId) {
    headers['X-External-User-Id'] = externalUserId
  }
  return headers
}

function resolveBackendAssetUrl(value) {
  const normalizedValue = String(value || '').trim()
  if (!normalizedValue) {
    return ''
  }
  if (/^https?:\/\//i.test(normalizedValue)) {
    return normalizedValue
  }

  const normalizedBase = String(API_BASE_URL || '').replace(/\/+$/, '')
  const normalizedPath = normalizedValue.startsWith('/') ? normalizedValue : `/${normalizedValue}`
  return `${normalizedBase}${normalizedPath}`
}

function appendQueryParam(url, key, value) {
  const normalizedUrl = String(url || '').trim()
  const normalizedKey = String(key || '').trim()
  const normalizedValue = String(value || '').trim()
  if (!normalizedUrl || !normalizedKey || !normalizedValue) {
    return normalizedUrl
  }

  const separator = normalizedUrl.includes('?') ? '&' : '?'
  return `${normalizedUrl}${separator}${encodeURIComponent(normalizedKey)}=${encodeURIComponent(normalizedValue)}`
}

function extractFileExtension(value, fallback = '.wav') {
  const normalizedValue = String(value || '').trim()
  const withoutQuery = normalizedValue.split('?')[0].split('#')[0]
  const match = withoutQuery.match(/\.([a-zA-Z0-9]+)$/)
  return match ? `.${match[1].toLowerCase()}` : fallback
}

function extensionFromContentType(value) {
  const normalizedValue = String(value || '').trim().toLowerCase()
  if (!normalizedValue) {
    return ''
  }
  if (normalizedValue.includes('audio/mpeg') || normalizedValue.includes('audio/mp3') || normalizedValue.includes('audio/mpga')) {
    return '.mp3'
  }
  if (normalizedValue.includes('audio/wav') || normalizedValue.includes('audio/x-wav')) {
    return '.wav'
  }
  if (normalizedValue.includes('audio/ogg')) {
    return '.ogg'
  }
  if (normalizedValue.includes('audio/mp4') || normalizedValue.includes('audio/m4a')) {
    return '.m4a'
  }
  return ''
}

function resolvePlayableAudioExtension({ audioUrl, contentType }) {
  return extractFileExtension(audioUrl, '') || extensionFromContentType(contentType) || '.mp3'
}

function isMpWeixin() {
  try {
    const info = uni.getSystemInfoSync()
    return (info && info.uniPlatform === 'mp-weixin') || (typeof wx !== 'undefined' && typeof wx.request === 'function')
  } catch (error) {
    return typeof wx !== 'undefined' && typeof wx.request === 'function'
  }
}

function buildLocalAudioFilePath(seed, extension) {
  const safeExtension = String(extension || '.wav').startsWith('.') ? String(extension || '.wav') : `.${extension}`
  const normalizedSeed = String(seed || '').replace(/[^a-zA-Z0-9_-]/g, '') || `${Date.now()}`
  if (typeof wx === 'undefined' || !wx.env || !wx.env.USER_DATA_PATH) {
    throw new Error('当前环境不支持本地音频缓存')
  }
  return `${wx.env.USER_DATA_PATH}/tts_${normalizedSeed}${safeExtension}`
}

function writeMiniProgramFile(filePath, data) {
  return new Promise((resolve, reject) => {
    if (typeof wx === 'undefined' || typeof wx.getFileSystemManager !== 'function') {
      reject(new Error('当前环境不支持本地文件写入'))
      return
    }

    const fileSystemManager = wx.getFileSystemManager()
    fileSystemManager.writeFile({
      filePath,
      data,
      success: () => resolve(filePath),
      fail: reject
    })
  })
}

function copyMiniProgramFile(sourcePath, destinationPath) {
  return new Promise((resolve, reject) => {
    if (typeof wx === 'undefined' || typeof wx.getFileSystemManager !== 'function') {
      reject(new Error('当前环境不支持本地文件复制'))
      return
    }

    const fileSystemManager = wx.getFileSystemManager()
    if (typeof fileSystemManager.copyFile === 'function') {
      fileSystemManager.copyFile({
        srcPath: sourcePath,
        destPath: destinationPath,
        success: () => resolve(destinationPath),
        fail: reject
      })
      return
    }

    fileSystemManager.readFile({
      filePath: sourcePath,
      success: async (res) => {
        try {
          const finalPath = await writeMiniProgramFile(destinationPath, res.data)
          resolve(finalPath)
        } catch (error) {
          reject(error)
        }
      },
      fail: reject
    })
  })
}

function removeMiniProgramFile(filePath) {
  return new Promise((resolve) => {
    if (!filePath || typeof wx === 'undefined' || typeof wx.getFileSystemManager !== 'function') {
      resolve()
      return
    }

    const fileSystemManager = wx.getFileSystemManager()
    if (typeof fileSystemManager.unlink !== 'function') {
      resolve()
      return
    }

    fileSystemManager.unlink({
      filePath,
      success: () => resolve(),
      fail: () => resolve()
    })
  })
}

async function persistMiniProgramTempAudio(tempFilePath, { cacheKey, audioUrl, contentType }) {
  const destinationPath = buildLocalAudioFilePath(
    cacheKey || Date.now(),
    resolvePlayableAudioExtension({ audioUrl, contentType })
  )
  await removeMiniProgramFile(destinationPath)
  return copyMiniProgramFile(tempFilePath, destinationPath)
}

function downloadMiniProgramFile(url, cacheKey) {
  return new Promise((resolve, reject) => {
    if (typeof uni.downloadFile !== 'function') {
      reject(new Error('当前环境不支持音频下载'))
      return
    }

    uni.downloadFile({
      url,
      header: {
        ...authHeader()
      },
      timeout: 300000,
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(createStreamError(normalizeErrorPayload(res.data), '音频下载失败'))
          return
        }

        const tempFilePath = String(res.tempFilePath || res.filePath || '').trim()
        if (!tempFilePath) {
          reject(new Error('音频下载成功但未返回本地文件路径'))
          return
        }

        persistMiniProgramTempAudio(tempFilePath, {
          cacheKey,
          audioUrl: url,
          contentType: res.header && (res.header['Content-Type'] || res.header['content-type'])
        })
          .then(resolve)
          .catch((error) => {
            console.warn('Mini-program audio persist fallback to temp file', {
              tempFilePath,
              error
            })
            resolve(tempFilePath)
          })
      },
      fail: reject
    })
  })
}

export function prepareAudioSourceForPlayback({ audioUrl, cacheKey }) {
  const resolvedUrl = resolveBackendAssetUrl(audioUrl)
  if (!resolvedUrl || !isMpWeixin()) {
    return Promise.resolve(resolvedUrl)
  }

  if (typeof uni.downloadFile === 'function') {
    return downloadMiniProgramFile(resolvedUrl, cacheKey)
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url: resolvedUrl,
      method: 'GET',
      header: {
        ...authHeader()
      },
      responseType: 'arraybuffer',
      timeout: 300000,
      success: async (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(createStreamError(normalizeErrorPayload(res.data), '音频下载失败'))
          return
        }

        try {
          const localPath = buildLocalAudioFilePath(
            cacheKey || Date.now(),
            resolvePlayableAudioExtension({
              audioUrl: resolvedUrl,
              contentType: res.header && (res.header['Content-Type'] || res.header['content-type'])
            })
          )
          const playablePath = await writeMiniProgramFile(localPath, res.data)
          resolve(playablePath)
        } catch (error) {
          reject(error)
        }
      },
      fail: reject
    })
  })
}

export function supportsDirectAudioPlayback(audioUrl) {
  const resolvedUrl = resolveBackendAssetUrl(audioUrl || API_BASE_URL)
  if (!resolvedUrl) {
    return false
  }
  if (!isMpWeixin()) {
    return true
  }
  return /^https:\/\//i.test(resolvedUrl)
}

export function buildAuthorizedAudioUrl(audioUrl) {
  const resolvedUrl = resolveBackendAssetUrl(audioUrl)
  if (!resolvedUrl) {
    return ''
  }

  const authToken = getAuthToken()
  if (!authToken) {
    return resolvedUrl
  }
  return appendQueryParam(resolvedUrl, 'access_token', authToken)
}

function jsonHeaders() {
  return {
    'Content-Type': 'application/json',
    ...authHeader()
  }
}

function callHandler(handler, payload) {
  if (typeof handler !== 'function') {
    return Promise.resolve()
  }
  return Promise.resolve(handler(payload))
}

function createStreamError(payload, fallbackDetail = '发送失败') {
  const detail = payload && typeof payload === 'object'
    ? (payload.detail || payload.message || fallbackDetail)
    : String(payload || fallbackDetail)
  const error = new Error(detail)
  error.payload = payload
  return error
}

function toUint8Array(value) {
  if (!value) {
    return new Uint8Array(0)
  }
  if (value instanceof Uint8Array) {
    return value
  }
  if (typeof ArrayBuffer !== 'undefined') {
    if (value instanceof ArrayBuffer) {
      return new Uint8Array(value)
    }
    if (typeof ArrayBuffer.isView === 'function' && ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
    }
  }
  if (Array.isArray(value)) {
    return Uint8Array.from(value)
  }
  return new Uint8Array(0)
}

function appendUint8Arrays(left, right) {
  if (!left.length) {
    return right
  }
  if (!right.length) {
    return left
  }
  const merged = new Uint8Array(left.length + right.length)
  merged.set(left)
  merged.set(right, left.length)
  return merged
}

function decodeUtf8Bytes(bytes) {
  if (!bytes || !bytes.length) {
    return ''
  }
  if (typeof TextDecoder !== 'undefined') {
    return new TextDecoder('utf-8').decode(bytes)
  }

  let result = ''
  for (let index = 0; index < bytes.length; index += 1) {
    const firstByte = bytes[index]

    if (firstByte < 0x80) {
      result += String.fromCharCode(firstByte)
      continue
    }

    if ((firstByte & 0xE0) === 0xC0) {
      const secondByte = bytes[index + 1]
      if (typeof secondByte === 'undefined') {
        result += '\uFFFD'
        break
      }
      const codePoint = ((firstByte & 0x1F) << 6) | (secondByte & 0x3F)
      result += String.fromCharCode(codePoint)
      index += 1
      continue
    }

    if ((firstByte & 0xF0) === 0xE0) {
      const secondByte = bytes[index + 1]
      const thirdByte = bytes[index + 2]
      if (typeof secondByte === 'undefined' || typeof thirdByte === 'undefined') {
        result += '\uFFFD'
        break
      }
      const codePoint = ((firstByte & 0x0F) << 12) | ((secondByte & 0x3F) << 6) | (thirdByte & 0x3F)
      result += String.fromCharCode(codePoint)
      index += 2
      continue
    }

    if ((firstByte & 0xF8) === 0xF0) {
      const secondByte = bytes[index + 1]
      const thirdByte = bytes[index + 2]
      const fourthByte = bytes[index + 3]
      if (
        typeof secondByte === 'undefined' ||
        typeof thirdByte === 'undefined' ||
        typeof fourthByte === 'undefined'
      ) {
        result += '\uFFFD'
        break
      }
      const codePoint = ((firstByte & 0x07) << 18) |
        ((secondByte & 0x3F) << 12) |
        ((thirdByte & 0x3F) << 6) |
        (fourthByte & 0x3F)
      result += String.fromCodePoint(codePoint)
      index += 3
      continue
    }

    result += '\uFFFD'
  }

  return result
}

function findSseBoundary(bytes) {
  for (let index = 0; index < bytes.length; index += 1) {
    if (
      bytes[index] === 0x0D &&
      index + 3 < bytes.length &&
      bytes[index + 1] === 0x0A &&
      bytes[index + 2] === 0x0D &&
      bytes[index + 3] === 0x0A
    ) {
      return { index, length: 4 }
    }

    if (bytes[index] === 0x0A && index + 1 < bytes.length && bytes[index + 1] === 0x0A) {
      return { index, length: 2 }
    }
  }
  return null
}

function parseSseEventBlock(block) {
  if (!block) {
    return null
  }

  let eventName = 'message'
  const dataLines = []

  block.split(/\r?\n/).forEach((line) => {
    if (!line || line.startsWith(':')) {
      return
    }
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim() || 'message'
      return
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^\s/, ''))
    }
  })

  if (!dataLines.length) {
    return null
  }

  const rawData = dataLines.join('\n').trim()
  if (!rawData) {
    return null
  }

  if (rawData === '[DONE]') {
    return { event: 'done', data: {} }
  }

  try {
    return {
      event: eventName,
      data: JSON.parse(rawData)
    }
  } catch (error) {
    return {
      event: eventName,
      data: { text: rawData }
    }
  }
}

function normalizeFinalPayload(payload, fallbackSessionId) {
  const source = payload && typeof payload === 'object'
    ? payload
    : { reply: String(payload || '') }
  const replyValue = source.reply != null
    ? source.reply
    : (source.reply_text != null ? source.reply_text : source.text)

  return {
    ...source,
    reply: String(replyValue || ''),
    session_id: source.session_id || fallbackSessionId || '',
    meta: source.meta && typeof source.meta === 'object' ? source.meta : {}
  }
}

function normalizeErrorPayload(payload) {
  if (payload && typeof payload === 'object') {
    if (!payload.detail && payload.errMsg) {
      return {
        ...payload,
        detail: payload.errMsg
      }
    }
    return payload
  }
  return { detail: String(payload || '发送失败') }
}

function createStreamState(fallbackSessionId) {
  return {
    fallbackSessionId: fallbackSessionId || '',
    sessionId: fallbackSessionId || '',
    finalPayload: null,
    errorPayload: null,
    sawEvent: false,
    doneSeen: false
  }
}

async function dispatchParsedEvent(parsedEvent, state, handlers) {
  if (!parsedEvent) {
    return
  }

  state.sawEvent = true
  const payload = parsedEvent.data && typeof parsedEvent.data === 'object'
    ? parsedEvent.data
    : { text: String(parsedEvent.data || '') }

  if (payload.session_id) {
    state.sessionId = payload.session_id
  }

  if (parsedEvent.event === 'start') {
    await callHandler(handlers.onStart, payload)
    return
  }

  if (parsedEvent.event === 'delta') {
    await callHandler(handlers.onDelta, payload)
    return
  }

  if (parsedEvent.event === 'final') {
    state.finalPayload = normalizeFinalPayload(payload, state.sessionId || state.fallbackSessionId)
    state.sessionId = state.finalPayload.session_id || state.sessionId
    await callHandler(handlers.onFinal, state.finalPayload)
    return
  }

  if (parsedEvent.event === 'done') {
    state.doneSeen = true
    await callHandler(handlers.onDone, payload)
    return
  }

  if (parsedEvent.event === 'error') {
    state.errorPayload = normalizeErrorPayload(payload)
    await callHandler(handlers.onError, state.errorPayload)
    throw createStreamError(state.errorPayload)
  }
}

async function finalizeStreamState(state, handlers) {
  if (state.errorPayload) {
    throw createStreamError(state.errorPayload)
  }

  if (!state.sawEvent && !state.finalPayload) {
    throw createStreamError({ detail: 'Empty stream response' })
  }

  const finalPayload = state.finalPayload || normalizeFinalPayload(
    { reply: '', session_id: state.sessionId || state.fallbackSessionId || '' },
    state.sessionId || state.fallbackSessionId
  )

  if (!state.doneSeen) {
    await callHandler(handlers.onDone, { session_id: finalPayload.session_id })
  }

  return finalPayload
}

function createSseChunkProcessor(onParsedEvent) {
  let buffer = new Uint8Array(0)

  return {
    async pushChunk(chunk) {
      const chunkBytes = toUint8Array(chunk)
      if (!chunkBytes.length) {
        return
      }

      buffer = appendUint8Arrays(buffer, chunkBytes)
      let boundary = findSseBoundary(buffer)

      while (boundary) {
        const blockText = decodeUtf8Bytes(buffer.slice(0, boundary.index)).trim()
        buffer = buffer.slice(boundary.index + boundary.length)

        const parsedEvent = parseSseEventBlock(blockText)
        if (parsedEvent) {
          await onParsedEvent(parsedEvent)
        }

        boundary = findSseBoundary(buffer)
      }
    },

    async flush() {
      const trailingText = decodeUtf8Bytes(buffer).trim()
      buffer = new Uint8Array(0)
      const parsedEvent = parseSseEventBlock(trailingText)
      if (parsedEvent) {
        await onParsedEvent(parsedEvent)
      }
    }
  }
}

async function synthesizeLifecycleFromPayload(payload, handlers, fallbackSessionId) {
  const finalPayload = normalizeFinalPayload(payload, fallbackSessionId)
  await callHandler(handlers.onStart, {
    session_id: finalPayload.session_id,
    meta: finalPayload.meta || {}
  })
  await callHandler(handlers.onFinal, finalPayload)
  await callHandler(handlers.onDone, { session_id: finalPayload.session_id })
  return finalPayload
}

async function parseCompletedStreamText(text, handlers, fallbackSessionId) {
  const normalizedText = String(text || '')
  const state = createStreamState(fallbackSessionId)

  const blocks = normalizedText
    .split(/\r?\n\r?\n/)
    .map((block) => block.trim())
    .filter(Boolean)

  let sawSse = false
  for (const block of blocks) {
    const parsedEvent = parseSseEventBlock(block)
    if (!parsedEvent) {
      continue
    }
    sawSse = true
    await dispatchParsedEvent(parsedEvent, state, handlers)
  }

  if (sawSse) {
    return finalizeStreamState(state, handlers)
  }

  const trimmedText = normalizedText.trim()
  if (!trimmedText) {
    throw createStreamError({ detail: 'Empty stream response' })
  }

  try {
    return await synthesizeLifecycleFromPayload(JSON.parse(trimmedText), handlers, fallbackSessionId)
  } catch (error) {
    return synthesizeLifecycleFromPayload({ reply: trimmedText, session_id: fallbackSessionId || '' }, handlers, fallbackSessionId)
  }
}

async function parseCompletedStreamData(data, handlers, fallbackSessionId) {
  const binaryData = toUint8Array(data)
  if (binaryData.length) {
    return parseCompletedStreamText(decodeUtf8Bytes(binaryData), handlers, fallbackSessionId)
  }

  if (typeof data === 'string') {
    return parseCompletedStreamText(data, handlers, fallbackSessionId)
  }

  if (data && typeof data === 'object') {
    return synthesizeLifecycleFromPayload(data, handlers, fallbackSessionId)
  }

  throw createStreamError({ detail: 'Empty stream response' })
}

function getStreamRequestOptions(url, data) {
  return {
    url,
    method: 'POST',
    header: jsonHeaders(),
    data,
    timeout: 600000
  }
}

function isWeChatMiniPlatform() {
  return isMpWeixin()
}

function parseFetchErrorPayload(response) {
  const contentType = (response.headers.get('content-type') || '').toLowerCase()
  if (contentType.includes('application/json')) {
    return response.json().catch(() => ({ detail: `HTTP ${response.status}` }))
  }
  return response.text()
    .then((text) => ({ detail: text || `HTTP ${response.status}` }))
    .catch(() => ({ detail: `HTTP ${response.status}` }))
}

async function streamRequestViaFetch(url, data, sessionId, handlers) {
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
  const timeoutId = controller && typeof setTimeout === 'function'
    ? setTimeout(() => controller.abort(), 600000)
    : null

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(data),
      signal: controller ? controller.signal : undefined
    })

    if (!response.ok) {
      throw createStreamError(await parseFetchErrorPayload(response))
    }

    if (!response.body || typeof response.body.getReader !== 'function') {
      const contentType = (response.headers.get('content-type') || '').toLowerCase()
      if (contentType.includes('application/json')) {
        return synthesizeLifecycleFromPayload(await response.json(), handlers, sessionId)
      }
      return parseCompletedStreamText(await response.text(), handlers, sessionId)
    }

    const state = createStreamState(sessionId)
    const processor = createSseChunkProcessor((parsedEvent) => dispatchParsedEvent(parsedEvent, state, handlers))
    const reader = response.body.getReader()

    while (true) {
      const { value, done } = await reader.read()
      if (value && value.length) {
        await processor.pushChunk(value)
      }
      if (done) {
        break
      }
    }

    await processor.flush()
    return finalizeStreamState(state, handlers)
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }
  }
}

function streamRequestViaUniRequest(url, data, sessionId, handlers, enableChunked) {
  return new Promise((resolve, reject) => {
    const state = createStreamState(sessionId)
    let settled = false
    let processor = null
    let processingQueue = Promise.resolve()

    const resolveOnce = (value) => {
      if (settled) {
        return
      }
      settled = true
      resolve(value)
    }

    const rejectOnce = (error) => {
      if (settled) {
        return
      }
      settled = true
      reject(error)
    }

    const requestOptions = getStreamRequestOptions(url, data)
    if (enableChunked) {
      requestOptions.enableChunked = true
      requestOptions.responseType = 'arraybuffer'
    }

    requestOptions.success = (res) => {
      Promise.resolve()
        .then(async () => {
          if (settled) {
            return
          }
          if (res.statusCode !== 200) {
            throw createStreamError(normalizeErrorPayload(res.data))
          }

          await processingQueue
          if (settled) {
            return
          }

          if (processor) {
            await processor.flush()
          }

          if (state.sawEvent) {
            resolveOnce(await finalizeStreamState(state, handlers))
            return
          }

          resolveOnce(await parseCompletedStreamData(res.data, handlers, sessionId))
        })
        .catch(rejectOnce)
    }

    requestOptions.fail = (error) => {
      rejectOnce(createStreamError(normalizeErrorPayload(error)))
    }

    const requestTask = uni.request(requestOptions)
    if (enableChunked && requestTask && typeof requestTask.onChunkReceived === 'function') {
      processor = createSseChunkProcessor((parsedEvent) => dispatchParsedEvent(parsedEvent, state, handlers))
      requestTask.onChunkReceived((chunkResponse) => {
        processingQueue = processingQueue
          .then(() => processor.pushChunk(chunkResponse.data))
          .catch((error) => {
            try {
              if (typeof requestTask.abort === 'function') {
                requestTask.abort()
              }
            } catch (abortError) {
            }
            rejectOnce(error)
          })
      })
    }
  })
}

export function streamChatReply({ type, content, sessionId, onStart, onDelta, onFinal, onDone, onError }) {
  const handlers = { onStart, onDelta, onFinal, onDone, onError }
  const url = `${API_BASE_URL}/api/chat/stream`
  const data = { type, content, session_id: sessionId || null }

  if (isWeChatMiniPlatform()) {
    return streamRequestViaUniRequest(url, data, sessionId, handlers, true)
  }

  if (typeof fetch === 'function') {
    return streamRequestViaFetch(url, data, sessionId, handlers)
  }

  return streamRequestViaUniRequest(url, data, sessionId, handlers, false)
}

export function streamRegenerateReply({ sessionId, messageId, onStart, onDelta, onFinal, onDone, onError }) {
  const handlers = { onStart, onDelta, onFinal, onDone, onError }
  const data = {
    session_id: sessionId || null
  }
  const normalizedMessageId = String(messageId || '').trim()
  if (/^\d+$/.test(normalizedMessageId)) {
    data.message_id = normalizedMessageId
  }
  const url = `${API_BASE_URL}/api/chat/regenerate/stream`

  if (isWeChatMiniPlatform()) {
    return streamRequestViaUniRequest(url, data, sessionId, handlers, true)
  }

  if (typeof fetch === 'function') {
    return streamRequestViaFetch(url, data, sessionId, handlers)
  }

  return streamRequestViaUniRequest(url, data, sessionId, handlers, false)
}

export function sendTextOrForm({ type, content, sessionId }) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/chat`,
      method: 'POST',
      header: jsonHeaders(),
      data: { type, content, session_id: sessionId || null },
      timeout: 600000,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

export function createSession({ forceNew = false, sessionId = '' } = {}) {
  const headers = {
    ...authHeader()
  }
  const queryParts = []
  if (forceNew) {
    queryParts.push('force_new=1')
  }
  if (sessionId) {
    queryParts.push(`session_id=${encodeURIComponent(sessionId)}`)
  }
  const query = queryParts.length ? `?${queryParts.join('&')}` : ''
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/chatbot${query}`,
      method: 'GET',
      header: headers,
      timeout: 60000,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

export function listChatSessions() {
  const headers = {
    ...authHeader()
  }
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/chat/sessions`,
      method: 'GET',
      header: headers,
      timeout: 60000,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

export function renameChatSession(sessionId, title, { currentSessionId = '' } = {}) {
  const headers = {
    ...jsonHeaders()
  }
  let url = `${API_BASE_URL}/api/chat/sessions/${encodeURIComponent(sessionId || '')}`
  if (currentSessionId) {
    url = `${url}?current_session_id=${encodeURIComponent(currentSessionId)}`
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url,
      method: 'PATCH',
      header: headers,
      data: { title: title == null ? null : String(title) },
      timeout: 60000,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

export function deleteChatSession(sessionId, { currentSessionId = '' } = {}) {
  const headers = {
    ...authHeader()
  }
  let url = `${API_BASE_URL}/api/chat/sessions/${encodeURIComponent(sessionId || '')}`
  if (currentSessionId) {
    url = `${url}?current_session_id=${encodeURIComponent(currentSessionId)}`
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url,
      method: 'DELETE',
      header: headers,
      timeout: 60000,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

function _parseUploadResponse(res) {
  let data = res.data
  if (typeof data === 'string') {
    data = JSON.parse(data)
  }

  if (res.statusCode < 200 || res.statusCode >= 300) {
    const detail = data && typeof data === 'object'
      ? (data.detail || data.message)
      : ''
    const error = new Error(detail || `Upload failed (${res.statusCode})`)
    error.statusCode = res.statusCode
    error.payload = data
    throw error
  }

  return data
}

export function uploadFileOnly({ type, filePath }) {
  const headers = {
    ...authHeader()
  }

  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${API_BASE_URL}/api/files/upload`,
      filePath,
      name: 'file',
      header: headers,
      formData: {
        type
      },
      success: (res) => {
        try {
          const data = _parseUploadResponse(res)
          if (!data || !data.file_id) {
            throw new Error('Upload succeeded without file_id')
          }
          resolve(data)
        } catch (e) {
          reject(e)
        }
      },
      fail: reject
    })
  })
}

export function sendMixedMessage({ text, imageFileIds, videoFileIds, audioFileId, sessionId }) {  
  const content = {}
  if (text && text.trim()) content.text = text.trim()
  if (imageFileIds && imageFileIds.length) content.image_file_ids = imageFileIds
  if (videoFileIds && videoFileIds.length) content.video_file_ids = videoFileIds
  if (audioFileId) content.audio_file_id = audioFileId
  return sendTextOrForm({ type: 'mixed', content, sessionId })  
}

export function transcribeAudio({ filePath, fileName }) {
  const headers = {
    ...authHeader()
  }

  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${API_BASE_URL}/api/transcribe`,
      filePath,
      name: 'file',
      header: headers,
      formData: {},
      success: (res) => {
        try {
          const data = _parseUploadResponse(res)
          resolve(data)
        } catch (e) {
          reject(e)
        }
      },
      fail: reject
    })
  })
}

export function synthesizeSpeech({ text, messageId, languageType }) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/speak`,
      method: 'POST',
      header: jsonHeaders(),
      data: {
        text,
        message_id: messageId || '',
        language_type: languageType || undefined
      },
      timeout: 300000,
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(createStreamError(normalizeErrorPayload(res.data), '语音合成失败'))
          return
        }

        const payload = res.data && typeof res.data === 'object' ? res.data : {}
        resolve({
          ...payload,
          audio_url: resolveBackendAssetUrl(payload.audio_url || '')
        })
      },
      fail: reject
    })
  })
}

export function synthesizeSpeechStream({ text, messageId, languageType }) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/speak/stream`,
      method: 'POST',
      header: jsonHeaders(),
      data: {
        text,
        message_id: messageId || '',
        language_type: languageType || undefined
      },
      timeout: 300000,
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(createStreamError(normalizeErrorPayload(res.data), '语音合成失败'))
          return
        }

        const payload = res.data && typeof res.data === 'object' ? res.data : {}
        resolve({
          ...payload,
          stream_url: resolveBackendAssetUrl(payload.stream_url || '')
        })
      },
      fail: reject
    })
  })
}

export function regenerateReply({ sessionId, messageId }) {
  const data = {
    session_id: sessionId
  }
  const normalizedMessageId = String(messageId || '').trim()
  if (/^\d+$/.test(normalizedMessageId)) {
    data.message_id = normalizedMessageId
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/chat/regenerate`,
      method: 'POST',
      header: jsonHeaders(),
      data,
      timeout: 300000,
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(createStreamError(normalizeErrorPayload(res.data), '重答失败'))
          return
        }

        resolve(res.data && typeof res.data === 'object' ? res.data : {})
      },
      fail: reject
    })
  })
}

export function wxLogin(code) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/auth/wx_login`,
      method: 'POST',
      data: { code },
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

export function updateWxUser(payload) {
  const headers = {
    'Content-Type': 'application/json',
    ...authHeader()
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/auth/update_wx_user`,
      method: 'POST',
      header: headers,
      data: payload,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

export function getPhone(payload) {
  const headers = {
    ...authHeader()
  }
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}/api/auth/get-phone`,
      method: 'POST',
      header: headers,
      data: payload,
      success: (res) => {
        if (res.statusCode === 200) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

