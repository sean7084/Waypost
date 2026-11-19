// Item 7 - TTS playback: /speak buffered-download fallback (the dev http path),
// the /speak/stream direct endpoint, local cache file, and the stop toggle.
// Synthesis uses the real provider (Qwen primary is in arrears -> Volcengine
// seed-tts-2.0 fallback). Audibility on a real speaker is MANUAL.
const lib = require('./lib');

const MSG_ID = 'tts-automator-msg';
const TEXT = '你好，这是泓问的语音合成自动化测试。';

module.exports = {
  id: 7,
  key: '07-tts',
  title: 'TTS 两路径（speak/stream 与 speak 回退）/ 停止',
  mode: 'mock',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    const call = (m, ...a) => lib.callPageMethod(mini, m, ...a);

    // A known assistant text message to speak (avoids an extra LLM turn).
    await call('appendMessage', { id: MSG_ID, role: 'assistant', type: 'text', content: TEXT });
    await call('syncMessageUi');

    // --- Path 1: dev http forces the buffered /speak download fallback.
    const played = await call('playAssistantSpeech', { messageId: MSG_ID });
    const cachedUrl = await lib.waitFor(
      async () => {
        const url = await call('cachedSpeechUrl', MSG_ID);
        return url ? url : null;
      },
      { timeout: 120000, interval: 500, label: 'TTS synthesized + cached' }
    );
    lib.assert(played === true, `playAssistantSpeech started playback (returned ${played})`);
    lib.assert(/tts_/.test(cachedUrl), `cached speech is a persisted local TTS file (got ${cachedUrl})`);
    const fileOk = await mini.evaluate((p) => {
      try {
        wx.getFileSystemManager().accessSync(p);
        return true;
      } catch (e) {
        return false;
      }
    }, cachedUrl);
    lib.assert(fileOk, 'cached TTS file exists on the local filesystem');
    notes.push(`speak fallback: /speak -> download -> persisted cache ${cachedUrl} (exists=${fileOk}); Qwen primary arrears -> Volcengine seed-tts-2.0`);
    evidence.push(await lib.shot(mini, '07-tts-cached'));

    // --- Stop toggle: start playback explicitly, then tap speak again to stop.
    await call('startSpeechPlayback', { messageId: MSG_ID, audioUrl: cachedUrl });
    let state = await chat.data('assistantActionState');
    lib.assert(state.activeMessageId === MSG_ID, 'activeMessageId set while playing');
    const stopped = await call('playAssistantSpeech', { messageId: MSG_ID });
    state = await chat.data('assistantActionState');
    lib.assert(stopped === true && state.activeMessageId === '', 'second speak tap stops playback (activeMessageId cleared)');
    notes.push('stop: re-invoking playAssistantSpeech on the active message clears assistantActionState.activeMessageId');
    evidence.push(await lib.shot(mini, '07-tts-stopped'));

    // --- Path 2: the /speak/stream direct endpoint (https path on prod).
    const stream = await mini.evaluate(async () => {
      const api = require('utils/api');
      const request = require('utils/request');
      const r = await api.synthesizeSpeechStream({ text: '泓问流式语音测试', messageId: 'tts-stream-test' });
      const url = request.buildAuthorizedAudioUrl(r.stream_url);
      const res = await new Promise((resolve, reject) =>
        wx.request({ url, method: 'GET', responseType: 'arraybuffer', success: resolve, fail: reject })
      );
      return {
        stream_url: r.stream_url,
        model: r.model,
        statusCode: res.statusCode,
        bytes: res.data ? res.data.byteLength : 0,
        contentType: (res.header && (res.header['Content-Type'] || res.header['content-type'])) || '',
      };
    });
    lib.assert(stream.stream_url, '/speak/stream returned a stream_url');
    lib.assert(stream.statusCode === 200 && stream.bytes > 0, `stream download 200 with audio bytes (got ${JSON.stringify(stream)})`);
    notes.push(`speak/stream: POST -> ${stream.stream_url} (model ${stream.model}); GET ${stream.statusCode}, ${stream.bytes} bytes, ${stream.contentType}`);

    notes.push('MANUAL(真机): 模拟器无声卡输出，需真机扫码确认两条路径实际可听、再次点击停止。');
    return { status: 'PASS', evidence, notes, manual: ['真机 TTS 可听性（speak/stream 直连与 speak 下载回退）+ 停止'] };
  },
};