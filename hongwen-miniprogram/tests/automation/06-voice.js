// Item 6 - voice recording: level meter, cancel/send paths, transcription回填,
// auto-speak. The simulator has no real microphone, so the recording lifecycle is
// MANUAL (real device via preview QR). We only assert the voice entry UI exists.
const lib = require('./lib');

module.exports = {
  id: 6,
  key: '06-voice',
  title: '语音录制（电平/取消/发送/转写/自动朗读）',
  mode: 'manual',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    const inputWrap = await chat.$('.input-wrap');
    lib.assert(inputWrap, 'composer input-wrap present (hosts input-bar with voice entry)');
    const meterBars = await mini.evaluate(() => {
      const page = getCurrentPages().pop();
      const comp = page.selectComponent('.input-wrap input-bar') || page.selectComponent('input-bar');
      return comp && comp.data.meterBars ? comp.data.meterBars.length : null;
    });
    evidence.push(await lib.shot(mini, '06-voice-entry'));
    notes.push(`composer mounted (input-wrap present, meterBars=${meterBars}); recording requires a real microphone (simulator has none)`);
    notes.push('电平算法 estimateVoiceLevel 是 input-bar 组件方法（非导出纯函数），未纳入 vitest；真机验证电平条随音量起伏。');
    return {
      status: 'MANUAL',
      evidence,
      notes,
      manual: [
        '真机扫码（preview-qr.jpg）进入聊天，点击语音入口开始录音',
        '对着麦克风说话，确认电平条随音量起伏（record-window meter-bar）',
        '点左侧 × 取消：不产生语音消息；点右侧 ↑ 发送：产生语音消息并转写回填',
        '确认转写文本作为用户消息提交，且回复自动朗读（autoSpeakReply）',
      ],
    };
  },
};