// Photo capture helper: chooseMedia -> compressImage -> saveFile.
//
// Compression runs at capture time, not upload time, because the app is
// offline-first: photos persist to local FS immediately and sync later via
// utils/request.js (uploadFile), which has no size/quality knob.
//
// Pipeline per photo:
//   1. chooseMedia with sizeType:['compressed'] (cheap first pass)
//   2. getImageInfo -> compute scale to cap the long edge
//   3. compressImage with quality + scaled dimensions (deterministic cap)
//   4. saveFile to persist for offline sync
// Each step falls back to the previous path on failure, so capture never breaks
// (PNG/HEIC that compressImage won't touch still gets resized or passed through).
const MAX_LONG_EDGE = 1920; // px — full-HD reviewable, ~300 KB at q80 for 12 MP JPEG
const QUALITY = 80; // JPEG only; ignored for PNG

function chooseMedia(opts) {
  return new Promise((resolve, reject) => {
    wx.chooseMedia({ ...opts, success: resolve, fail: reject });
  });
}

function getImageInfo(src) {
  return new Promise((resolve, reject) => {
    wx.getImageInfo({ src, success: resolve, fail: reject });
  });
}

function compressImage(opts) {
  return new Promise((resolve, reject) => {
    wx.compressImage({ ...opts, success: resolve, fail: reject });
  });
}

function saveFile(tempFilePath) {
  return new Promise((resolve) => {
    wx.getFileSystemManager().saveFile({
      tempFilePath,
      success: (s) => resolve(s.savedFilePath),
      fail: () => resolve(tempFilePath),
    });
  });
}

async function compressAndSave(tempFilePath) {
  let src = tempFilePath;
  try {
    const info = await getImageInfo(src);
    const scale = Math.min(1, MAX_LONG_EDGE / Math.max(info.width, info.height));
    const res = await compressImage({
      src,
      quality: QUALITY,
      compressedWidth: Math.round(info.width * scale),
      compressedHeight: Math.round(info.height * scale),
    });
    src = res.tempFilePath;
  } catch (_e) {
    // PNG / HEIC / getImageInfo failure: keep the sizeType-compressed temp path.
  }
  return saveFile(src);
}

// Pick photos, compress, and persist. Resolves with an array of saved file
// paths (same order as picked). Rejects on user cancel or genuine picker
// failure — callers should catch and treat as a no-op.
async function pickPhotos({ count = 1, sourceType = ['album', 'camera'] } = {}) {
  const res = await chooseMedia({
    count,
    mediaType: ['image'],
    sourceType,
    sizeType: ['compressed'],
  });
  return Promise.all(res.tempFiles.map((f) => compressAndSave(f.tempFilePath)));
}

module.exports = { pickPhotos, MAX_LONG_EDGE, QUALITY };
