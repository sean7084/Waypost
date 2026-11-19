// Item 5 - streaming reply (typewriter), triage quick_replies chips, attachment
// image preview. Requires the real Django SSE transport + providers (mocking
// wx.request cannot fake enableChunked framing). Runs against the harness :8001
// server with the Volcengine embed override so triage actually matches.
const lib = require('./lib');

async function lastAssistant(chat) {
  const msgs = await chat.data('messages');
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    if (msgs[i].role === 'assistant' && msgs[i].type === 'text') return msgs[i];
  }
  return null;
}

module.exports = {
  id: 5,
  key: '05-stream',
  title: '流式回复 / quick_replies / 附件预览',
  mode: 'auto',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    await lib.linkCustomer(mini);
    await lib.callPageMethod(mini, 'handleCreateNewSession');
    await lib.waitForData(chat, (d) => Boolean(d.currentSessionId) && !d.sending, { timeout: 15000, label: 'fresh session' });

    // --- A. typewriter: a general greeting routes to normal RAG chat (delta stream)
    const greet = '你好，很高兴认识你，请简单打个招呼介绍一下你自己';
    const streamPromise = lib.firePageMethod(mini, 'onSendText', { detail: { text: greet } });
    const samples = [];
    await lib.waitFor(
      async () => {
        const msg = await lastAssistant(chat);
        if (msg) samples.push(String(msg.content || '').length);
        const sending = await chat.data('sending');
        return sending === false && samples.length > 0 ? true : null;
      },
      { timeout: 120000, interval: 60, label: 'greeting stream complete' }
    );
    await streamPromise.catch(() => {});
    const finalMsg = await lastAssistant(chat);
    const finalLen = String(finalMsg.content || '').length;
    let growth = 0;
    for (let i = 1; i < samples.length; i += 1) if (samples[i] > samples[i - 1]) growth += 1;
    lib.assert(finalLen > 0, 'assistant reply non-empty');
    lib.assert(growth >= 2, `typewriter incremental deltas observed (growth samples=${growth}, lengths=${samples.slice(0, 12).join(',')})`);
    evidence.push(await lib.shot(mini, '05-typewriter'));
    notes.push(`typewriter: reply ${finalLen} chars, ${growth} incremental growth samples over ${samples.length} polls (real SSE deltas)`);
    notes.push('KB empty (no documents ingested) -> used_knowledge_base=false; typewriter still works on pure provider stream');

    // --- B. triage quick_replies: a network symptom matches the no-internet playbook
    const triageText = '我的iPad上不了网，Wi-Fi连不上，网页打不开';
    const triagePromise = lib.firePageMethod(mini, 'onSendText', { detail: { text: triageText } });
    await lib.waitFor(
      async () => {
        const sending = await chat.data('sending');
        if (sending) return null;
        const ml = await chat.$('.message-area');
        const chips = ml ? await ml.$$('.quick-reply-chip') : [];
        return chips.length > 0 ? chips : null;
      },
      { timeout: 120000, interval: 300, label: 'triage quick-reply chips' }
    );
    await triagePromise.catch(() => {});
    let ml = await chat.$('.message-area');
    let chips = await ml.$$('.quick-reply-chip');
    lib.assert(chips.length >= 2, `triage intake quick_replies rendered (got ${chips.length})`);
    const chipLabels = [];
    for (const c of chips) chipLabels.push((await c.text()).trim());
    evidence.push(await lib.shot(mini, '05-quick-replies'));
    notes.push(`quick_replies: triage intake chips [${chipLabels.join(' | ')}]`);

    // tap the first chip (iPad / iPhone -> ios) -> advances to the ios check step
    await chips[0].tap();
    await lib.waitFor(
      async () => {
        const sending = await chat.data('sending');
        if (sending) return null;
        const m = await chat.$('.message-area');
        const imgs = m ? await m.$$('.triage-attachment-img') : [];
        return imgs.length > 0 ? imgs : null;
      },
      { timeout: 120000, interval: 300, label: 'triage step attachment image' }
    );
    ml = await chat.$('.message-area');
    const imgs = await ml.$$('.triage-attachment-img');
    lib.assert(imgs.length >= 1, 'triage step attachment image rendered');
    const chips2 = await ml.$$('.quick-reply-chip');
    lib.assert(chips2.length >= 1, 'advanced triage step still offers quick_replies');
    evidence.push(await lib.shot(mini, '05-triage-attachment'));
    notes.push(`quick_reply tap advanced flow: attachment image + ${chips2.length} next-step chips`);

    // --- C. attachment preview via wx.previewImage (mocked to capture the call)
    await mini.evaluate(() => {
      wx.__previewCalls = [];
    });
    await mini.mockWxMethod('previewImage', function (opts) {
      (wx.__previewCalls = wx.__previewCalls || []).push(opts.urls || [opts.current]);
      return { errMsg: 'previewImage:ok' };
    });
    await imgs[0].tap();
    await lib.sleep(800);
    const calls = await mini.evaluate(() => wx.__previewCalls || []);
    await mini.restoreWxMethod('previewImage');
    lib.assert(calls.length >= 1, 'wx.previewImage invoked on attachment tap');
    lib.assert(calls[0] && calls[0][0], 'previewImage received a non-empty image URL');
    evidence.push(await lib.shot(mini, '05-attachment-preview'));
    notes.push(`attachment preview: wx.previewImage urls=${JSON.stringify(calls[0]).slice(0, 120)}`);

    return { status: 'PASS', evidence, notes };
  },
};