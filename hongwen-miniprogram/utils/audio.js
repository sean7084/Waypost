// TTS / voice-note playback helpers.
//
// Port of the grafted client's src/utils/api.js:53-292. WeChat's
// InnerAudioContext accepts a URL or a local file path only, and a remote
// protected URL needs the access token in the query string (it cannot set
// headers). Downloading first and persisting under USER_DATA_PATH gives a
// seekable local file that survives a re-play without a second network round
// trip; every step falls back to the previous path so playback never hard-fails.
const request = require('./request');

function extractFileExtension(value, fallback = '.wav') {
  const normalized = String(value || '').trim();
  const withoutQuery = normalized.split('?')[0].split('#')[0];
  const match = withoutQuery.match(/\.([a-zA-Z0-9]+)$/);
  return match ? `.${match[1].toLowerCase()}` : fallback;
}

function extensionFromContentType(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (!normalized) return '';
  if (normalized.includes('audio/mpeg') || normalized.includes('audio/mp3') || normalized.includes('audio/mpga')) {
    return '.mp3';
  }
  if (normalized.includes('audio/wav') || normalized.includes('audio/x-wav')) return '.wav';
  if (normalized.includes('audio/ogg')) return '.ogg';
  if (normalized.includes('audio/mp4') || normalized.includes('audio/m4a')) return '.m4a';
  return '';
}

function resolvePlayableAudioExtension({ audioUrl, contentType }) {
  return extractFileExtension(audioUrl, '') || extensionFromContentType(contentType) || '.mp3';
}

function userDataPath() {
  return typeof wx !== 'undefined' && wx.env && wx.env.USER_DATA_PATH ? wx.env.USER_DATA_PATH : '';
}

function buildLocalAudioFilePath(seed, extension) {
  const root = userDataPath();
  if (!root) throw new Error('当前环境不支持本地音频缓存');
  const safeExtension = String(extension || '.wav').startsWith('.')
    ? String(extension || '.wav')
    : `.${extension}`;
  const normalizedSeed = String(seed || '').replace(/[^a-zA-Z0-9_-]/g, '') || `${Date.now()}`;
  return `${root}/tts_${normalizedSeed}${safeExtension}`;
}

function fileSystemManager() {
  if (typeof wx === 'undefined' || typeof wx.getFileSystemManager !== 'function') return null;
  return wx.getFileSystemManager();
}

function writeFile(filePath, data) {
  return new Promise((resolve, reject) => {
    const fs = fileSystemManager();
    if (!fs) {
      reject(new Error('当前环境不支持本地文件写入'));
      return;
    }
    fs.writeFile({ filePath, data, success: () => resolve(filePath), fail: reject });
  });
}

function copyFile(sourcePath, destinationPath) {
  return new Promise((resolve, reject) => {
    const fs = fileSystemManager();
    if (!fs) {
      reject(new Error('当前环境不支持本地文件复制'));
      return;
    }
    if (typeof fs.copyFile === 'function') {
      fs.copyFile({ srcPath: sourcePath, destPath: destinationPath, success: () => resolve(destinationPath), fail: reject });
      return;
    }
    fs.readFile({
      filePath: sourcePath,
      success: (res) => {
        writeFile(destinationPath, res.data).then(resolve).catch(reject);
      },
      fail: reject,
    });
  });
}

function removeFile(filePath) {
  return new Promise((resolve) => {
    const fs = fileSystemManager();
    if (!filePath || !fs || typeof fs.unlink !== 'function') {
      resolve();
      return;
    }
    fs.unlink({ filePath, success: () => resolve(), fail: () => resolve() });
  });
}

async function persistTempAudio(tempFilePath, { cacheKey, audioUrl, contentType }) {
  const destinationPath = buildLocalAudioFilePath(
    cacheKey || Date.now(),
    resolvePlayableAudioExtension({ audioUrl, contentType })
  );
  await removeFile(destinationPath);
  return copyFile(tempFilePath, destinationPath);
}

// Download a remote TTS blob and persist it locally. Resolves with a playable
// path; on a persist failure it resolves with the download temp path instead.
async function prepareAudioSourceForPlayback({ audioUrl, cacheKey }) {
  const resolvedUrl = request.resolveAssetUrl(audioUrl);
  if (!resolvedUrl) return '';

  const res = await request.downloadFile(resolvedUrl);
  const tempFilePath = String(res.tempFilePath || res.filePath || '').trim();
  if (!tempFilePath) throw new Error('音频下载成功但未返回本地文件路径');

  try {
    return await persistTempAudio(tempFilePath, {
      cacheKey,
      audioUrl: resolvedUrl,
      contentType: res.header && (res.header['Content-Type'] || res.header['content-type']),
    });
  } catch (_e) {
    return tempFilePath;
  }
}

// InnerAudioContext can stream an https URL directly; plain http (the local
// Django dev server) must be downloaded first.
function supportsDirectAudioPlayback(audioUrl) {
  const resolved = request.resolveAssetUrl(audioUrl);
  if (!resolved) return false;
  return /^https:\/\//i.test(resolved);
}

// A recorded voice note lives on the local filesystem and must NOT be prefixed
// with the API origin or carry a token.
function isLocalAudioPath(filePath) {
  const value = String(filePath || '').trim();
  if (!value) return false;
  if (/^(wxfile:\/\/|http:\/\/tmp\/|https:\/\/tmp\/)/i.test(value)) return true;
  const root = userDataPath();
  return Boolean(root) && value.indexOf(root) === 0;
}

// Idempotent: history URLs already carry access_token from the chat page.
function playableAudioSrc(filePath) {
  const value = String(filePath || '').trim();
  if (!value) return '';
  if (isLocalAudioPath(value)) return value;
  return request.buildAuthorizedAudioUrl(value);
}

module.exports = {
  extractFileExtension,
  extensionFromContentType,
  resolvePlayableAudioExtension,
  buildLocalAudioFilePath,
  persistTempAudio,
  prepareAudioSourceForPlayback,
  supportsDirectAudioPlayback,
  isLocalAudioPath,
  playableAudioSrc,
};