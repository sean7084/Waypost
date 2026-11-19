// Hand-written UTF-8 decoding for the native WeChat mini program.
//
// Why this exists: wx.request with `enableChunked: true` delivers each SSE chunk
// as an ArrayBuffer, and the mini program runtime has NO TextDecoder (the
// uni-app build could lean on one). A streamed Chinese reply is multi-byte, and
// a chunk boundary can fall in the middle of a character, so decoding each chunk
// independently would corrupt the text. `createUtf8Decoder` holds an incomplete
// trailing sequence back until the next chunk completes it.
//
// Invalid bytes decode to U+FFFD (REPLACEMENT CHARACTER) rather than throwing -
// a garbled byte must never take down a live reply stream.

const REPLACEMENT = '\uFFFD';

// Accepts ArrayBuffer / TypedArray / plain byte array (wx hands back different
// shapes across base library versions) and normalizes to Uint8Array.
function toUint8Array(value) {
  if (!value) return new Uint8Array(0);
  if (value instanceof Uint8Array) return value;
  if (typeof ArrayBuffer !== 'undefined') {
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (typeof ArrayBuffer.isView === 'function' && ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
  }
  if (Array.isArray(value)) return Uint8Array.from(value);
  // A string is already text: callers must use it directly rather than round-trip
  // it through here (charCodeAt would truncate every non-Latin-1 character).
  return new Uint8Array(0);
}

function concatBytes(left, right) {
  if (!left.length) return right;
  if (!right.length) return left;
  const merged = new Uint8Array(left.length + right.length);
  merged.set(left);
  merged.set(right, left.length);
  return merged;
}

function isContinuation(byte) {
  return typeof byte === 'number' && (byte & 0xc0) === 0x80;
}

// Decodes one code point starting at `index`. Returns {codePoint, size} or
// {codePoint: null, size: n} for an invalid/truncated sequence, where `size` is
// the number of bytes consumed (always >= 1 so the caller makes progress).
function readCodePoint(bytes, index) {
  const first = bytes[index];

  if (first < 0x80) return { codePoint: first, size: 1 };

  if ((first & 0xe0) === 0xc0) {
    const second = bytes[index + 1];
    if (!isContinuation(second)) return { codePoint: null, size: 1 };
    const codePoint = ((first & 0x1f) << 6) | (second & 0x3f);
    // Overlong 2-byte form (would encode an ASCII character).
    if (codePoint < 0x80) return { codePoint: null, size: 2 };
    return { codePoint, size: 2 };
  }

  if ((first & 0xf0) === 0xe0) {
    const second = bytes[index + 1];
    const third = bytes[index + 2];
    if (!isContinuation(second) || !isContinuation(third)) {
      return { codePoint: null, size: isContinuation(second) ? 2 : 1 };
    }
    const codePoint = ((first & 0x0f) << 12) | ((second & 0x3f) << 6) | (third & 0x3f);
    // Overlong 3-byte form, or a UTF-16 surrogate half (illegal in UTF-8).
    if (codePoint < 0x800 || (codePoint >= 0xd800 && codePoint <= 0xdfff)) {
      return { codePoint: null, size: 3 };
    }
    return { codePoint, size: 3 };
  }

  if ((first & 0xf8) === 0xf0) {
    const second = bytes[index + 1];
    const third = bytes[index + 2];
    const fourth = bytes[index + 3];
    if (!isContinuation(second) || !isContinuation(third) || !isContinuation(fourth)) {
      let size = 1;
      if (isContinuation(second)) size = 2;
      if (isContinuation(second) && isContinuation(third)) size = 3;
      return { codePoint: null, size };
    }
    const codePoint =
      ((first & 0x07) << 18) | ((second & 0x3f) << 12) | ((third & 0x3f) << 6) | (fourth & 0x3f);
    // Overlong 4-byte form, or beyond the Unicode range.
    if (codePoint < 0x10000 || codePoint > 0x10ffff) return { codePoint: null, size: 4 };
    return { codePoint, size: 4 };
  }

  // Bare continuation byte or 0xF8..0xFF: never a legal lead byte.
  return { codePoint: null, size: 1 };
}

// Number of bytes a lead byte claims, or 0 when it is not a lead byte.
function sequenceLength(first) {
  if (first < 0x80) return 1;
  if ((first & 0xe0) === 0xc0) return 2;
  if ((first & 0xf0) === 0xe0) return 3;
  if ((first & 0xf8) === 0xf0) return 4;
  return 0;
}

function codePointToString(codePoint) {
  if (codePoint === null) return REPLACEMENT;
  // fromCodePoint handles astral planes (emoji) as a surrogate pair; fromCharCode
  // would silently truncate them.
  return codePoint > 0xffff ? String.fromCodePoint(codePoint) : String.fromCharCode(codePoint);
}

// One-shot decode of a complete byte sequence.
function decodeUtf8(value) {
  const bytes = toUint8Array(value);
  if (!bytes.length) return '';
  const parts = [];
  let index = 0;
  while (index < bytes.length) {
    const { codePoint, size } = readCodePoint(bytes, index);
    parts.push(codePointToString(codePoint));
    index += size;
  }
  return parts.join('');
}

// Streaming decode: push() returns only the text that is complete, keeping any
// partial trailing sequence buffered for the next chunk. flush() decodes what is
// left, emitting U+FFFD for a genuinely truncated tail.
function createUtf8Decoder() {
  let pending = new Uint8Array(0);

  return {
    push(chunk) {
      const bytes = concatBytes(pending, toUint8Array(chunk));
      pending = new Uint8Array(0);
      if (!bytes.length) return '';

      // Hold back a trailing lead byte whose sequence has not fully arrived.
      let end = bytes.length;
      const tailScanLimit = Math.min(3, bytes.length - 1);
      for (let back = 0; back <= tailScanLimit; back += 1) {
        const candidate = bytes.length - 1 - back;
        const claimed = sequenceLength(bytes[candidate]);
        if (claimed > 1 && candidate + claimed > bytes.length) {
          // Only hold back when the bytes so far are a plausible prefix.
          let plausible = true;
          for (let i = candidate + 1; i < bytes.length; i += 1) {
            if (!isContinuation(bytes[i])) {
              plausible = false;
              break;
            }
          }
          if (plausible) {
            end = candidate;
            break;
          }
        }
        if (claimed === 1) break; // ASCII byte: nothing partial behind it
      }

      pending = bytes.slice(end);
      return decodeUtf8(bytes.slice(0, end));
    },

    flush() {
      const tail = pending;
      pending = new Uint8Array(0);
      return decodeUtf8(tail);
    },

    get pendingLength() {
      return pending.length;
    },
  };
}

module.exports = {
  REPLACEMENT,
  toUint8Array,
  concatBytes,
  decodeUtf8,
  createUtf8Decoder,
};