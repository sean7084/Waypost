import { describe, it, expect } from 'vitest';
import { createRequire } from 'node:module';

// The utils are CommonJS mini program modules; load them through Node's require
// so the exact file that ships to WeChat is the file under test.
const require = createRequire(import.meta.url);
const { decodeUtf8, createUtf8Decoder, toUint8Array, concatBytes, REPLACEMENT } = require('./utf8.js');

const encode = (text) => new TextEncoder().encode(text);

describe('decodeUtf8', () => {
  it('decodes ASCII', () => {
    expect(decodeUtf8(encode('event: delta'))).toBe('event: delta');
  });

  it('decodes 2-byte sequences', () => {
    expect(decodeUtf8(encode('café ©'))).toBe('café ©');
  });

  it('decodes 3-byte Chinese sequences', () => {
    expect(decodeUtf8(encode('问渠清如许'))).toBe('问渠清如许');
  });

  it('decodes 4-byte astral sequences as surrogate pairs', () => {
    const text = '排障 😀🚀';
    expect(decodeUtf8(encode(text))).toBe(text);
  });

  it('decodes a mixed-script SSE payload', () => {
    const text = 'data: {"text":"重启路由器 — step 1 ✅"}';
    expect(decodeUtf8(encode(text))).toBe(text);
  });

  it('returns an empty string for empty input', () => {
    expect(decodeUtf8(new Uint8Array(0))).toBe('');
    expect(decodeUtf8(null)).toBe('');
    expect(decodeUtf8(undefined)).toBe('');
  });

  it('replaces a bare continuation byte', () => {
    expect(decodeUtf8(new Uint8Array([0x80]))).toBe(REPLACEMENT);
  });

  it('replaces an invalid lead byte', () => {
    expect(decodeUtf8(new Uint8Array([0xff, 0x41]))).toBe(`${REPLACEMENT}A`);
  });

  it('replaces a truncated trailing sequence', () => {
    // 0xE4 0xBD is the first two bytes of 你 (U+4F60).
    expect(decodeUtf8(new Uint8Array([0xe4, 0xbd]))).toBe(REPLACEMENT);
  });

  it('replaces a sequence whose continuation byte is not one', () => {
    expect(decodeUtf8(new Uint8Array([0xe4, 0x41, 0x42]))).toBe(`${REPLACEMENT}AB`);
  });

  it('rejects overlong encodings', () => {
    // 0xC0 0xAF is the overlong 2-byte form of '/'.
    expect(decodeUtf8(new Uint8Array([0xc0, 0xaf]))).toBe(REPLACEMENT);
    // 0xE0 0x80 0xAF is the overlong 3-byte form of '/'.
    expect(decodeUtf8(new Uint8Array([0xe0, 0x80, 0xaf]))).toBe(REPLACEMENT);
  });

  it('rejects encoded surrogate halves', () => {
    expect(decodeUtf8(new Uint8Array([0xed, 0xa0, 0x80]))).toBe(REPLACEMENT);
  });

  it('rejects code points beyond U+10FFFF', () => {
    expect(decodeUtf8(new Uint8Array([0xf4, 0x90, 0x80, 0x80]))).toBe(REPLACEMENT);
  });

  it('never loops forever on a malformed stream', () => {
    const bytes = new Uint8Array([0xf0, 0x80, 0x41, 0xe4, 0x42, 0x80, 0x43]);
    // 0xF0 0x80 is an invalid 4-byte prefix (maximal subpart -> one replacement),
    // then 'A', a lone 0xE4 lead, 'B', a bare continuation, 'C'.
    expect(decodeUtf8(bytes)).toBe(`${REPLACEMENT}A${REPLACEMENT}B${REPLACEMENT}C`);
  });
});

describe('createUtf8Decoder', () => {
  it('holds back a multi-byte character split across chunks', () => {
    const bytes = encode('问渠');
    const decoder = createUtf8Decoder();
    // Split inside the first character (3 bytes each).
    expect(decoder.push(bytes.slice(0, 1))).toBe('');
    expect(decoder.push(bytes.slice(1, 4))).toBe('问');
    expect(decoder.push(bytes.slice(4))).toBe('渠');
    expect(decoder.flush()).toBe('');
  });

  it('holds back a 4-byte character split across three chunks', () => {
    const bytes = encode('😀');
    const decoder = createUtf8Decoder();
    expect(decoder.push(bytes.slice(0, 1))).toBe('');
    expect(decoder.push(bytes.slice(1, 3))).toBe('');
    expect(decoder.push(bytes.slice(3))).toBe('😀');
  });

  it('reassembles a sentence fed one byte at a time', () => {
    const text = 'event: delta\ndata: {"text":"你好，问渠 😀"}\n\n';
    const bytes = encode(text);
    const decoder = createUtf8Decoder();
    let out = '';
    for (let i = 0; i < bytes.length; i += 1) {
      out += decoder.push(bytes.slice(i, i + 1));
    }
    out += decoder.flush();
    expect(out).toBe(text);
  });

  it('does not hold back a complete trailing character', () => {
    const decoder = createUtf8Decoder();
    expect(decoder.push(encode('渠'))).toBe('渠');
    expect(decoder.pendingLength).toBe(0);
  });

  it('emits a replacement for a genuinely truncated tail on flush', () => {
    const decoder = createUtf8Decoder();
    expect(decoder.push(new Uint8Array([0xe4, 0xbd]))).toBe('');
    expect(decoder.flush()).toBe(REPLACEMENT);
  });

  it('does not hold back an invalid lead byte', () => {
    const decoder = createUtf8Decoder();
    expect(decoder.push(new Uint8Array([0xff]))).toBe(REPLACEMENT);
    expect(decoder.pendingLength).toBe(0);
  });

  it('ignores empty chunks', () => {
    const decoder = createUtf8Decoder();
    expect(decoder.push(new Uint8Array(0))).toBe('');
    expect(decoder.push(null)).toBe('');
    expect(decoder.flush()).toBe('');
  });
});

describe('byte helpers', () => {
  it('normalizes ArrayBuffer, views and plain arrays', () => {
    const buffer = encode('AB').buffer;
    expect(Array.from(toUint8Array(buffer))).toEqual([65, 66]);
    expect(Array.from(toUint8Array(new Uint8Array([65, 66])))).toEqual([65, 66]);
    expect(Array.from(toUint8Array(new DataView(buffer)))).toEqual([65, 66]);
    expect(Array.from(toUint8Array([65, 66]))).toEqual([65, 66]);
  });

  it('respects a view byteOffset', () => {
    const buffer = encode('XAB').buffer;
    const view = new Uint8Array(buffer, 1, 2);
    expect(Array.from(toUint8Array(view))).toEqual([65, 66]);
  });

  it('does not lossily convert a string to bytes', () => {
    expect(toUint8Array('问渠').length).toBe(0);
  });

  it('concatenates without copying when one side is empty', () => {
    const left = new Uint8Array([1]);
    const right = new Uint8Array([2]);
    expect(concatBytes(new Uint8Array(0), right)).toBe(right);
    expect(concatBytes(left, new Uint8Array(0))).toBe(left);
    expect(Array.from(concatBytes(left, right))).toEqual([1, 2]);
  });
});