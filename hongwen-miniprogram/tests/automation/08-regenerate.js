// Item 8 - regenerate: only the latest reply is eligible, the action chip drives
// a real SSE re-answer, and a provider failure rolls back to the original text.
const lib = require('./lib');

async function sendAndWait(mini, chat, text) {
  const p = lib.firePageMethod(mini, 'onSendText', { detail: { text } });
  await lib.waitFor(
    async () => ((await chat.data('sending')) === false ? true : null),
    { timeout: 120000, interval: 200, label: `reply to "${text.slice(0, 12)}"` }
  );
  await p.catch(() => {});
  const id = await chat.data('latestAssistantId');
  const msgs = await chat.data('messages');
  const msg = msgs.find((m) => m.id === id);
  return { id, content: String((msg && msg.content) || '') };
}

async function regenerateChip(chat) {
  const ml = await chat.$('.message-area');
  const chips = await ml.$$('.action-chip');
  for (const chip of chips) {
    const action = await chip.attribute('data-action');
    if (action === 'regenerate') return chip;
  }
  return null;
}

module.exports = {
  id: 8,
  key: '08-regenerate',
  title: '重答 regenerate（仅最新可用 / 失败回滚）',
  mode: 'auto',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    const call = (m, ...a) => lib.callPageMethod(mini, m, ...a);
    await lib.linkCustomer(mini);
    await call('handleCreateNewSession');
    await lib.waitForData(chat, (d) => Boolean(d.currentSessionId) && !d.sending, { timeout: 15000, label: 'fresh session' });

    const first = await sendAndWait(mini, chat, '请用一句话说明路由器重启的作用');
    lib.assert(first.content, 'first assistant reply present');
    const second = await sendAndWait(mini, chat, '再用一句话说明如何检查网线是否插紧');
    lib.assert(second.id !== first.id, 'second reply is a distinct latest message');

    // older reply is not eligible
    const olderResult = await call('regenerateAssistantMessage', first.id);
    lib.assert(olderResult === false, 'regenerate on an older reply returns false');
    const msgsAfterOlder = await chat.data('messages');
    const olderMsg = msgsAfterOlder.find((m) => m.id === first.id);
    lib.assert(String(olderMsg.content) === first.content, 'older reply content unchanged');
    notes.push('eligibility: regenerateAssistantMessage(older id) -> false, "仅支持重答最新回复", content unchanged');

    // latest reply via the real action chip tap
    const chip = await regenerateChip(chat);
    lib.assert(chip, '.action-chip[data-action=regenerate] rendered for latest reply');
    const beforeLatest = second.content;
    await chip.tap();
    await lib.waitFor(
      async () => ((await chat.data('sending')) === false ? true : null),
      { timeout: 120000, interval: 200, label: 'regenerate stream complete' }
    );
    const latestId = await chat.data('latestAssistantId');
    const msgs = await chat.data('messages');
    const reMsg = msgs.find((m) => m.id === latestId);
    const reContent = String((reMsg && reMsg.content) || '');
    lib.assert(reContent.length > 0, 'regenerated reply non-empty');
    evidence.push(await lib.shot(mini, '08-regenerated'));
    notes.push(`chip tap regenerate: latest ${latestId} re-answered (${beforeLatest.length} -> ${reContent.length} chars, real SSE)`);

    // failure rollback: stub the provider stream to reject
    await mini.evaluate(() => {
      const api = require('utils/api');
      api.__origRegen = api.streamRegenerateReply;
      api.streamRegenerateReply = async () => {
        throw new Error('mock provider failure');
      };
    });
    const failResult = await call('regenerateAssistantMessage', latestId);
    const msgsRollback = await chat.data('messages');
    const rollbackMsg = msgsRollback.find((m) => m.id === latestId);
    lib.assert(failResult === false, 'regenerate returns false on provider failure');
    lib.assert(String(rollbackMsg.content).trim() === reContent.trim(), 'original text restored after provider failure');
    await mini.evaluate(() => {
      const api = require('utils/api');
      if (api.__origRegen) {
        api.streamRegenerateReply = api.__origRegen;
        delete api.__origRegen;
      }
    });
    evidence.push(await lib.shot(mini, '08-rollback'));
    notes.push('rollback: stubbed streamRegenerateReply rejection -> returns false, content restored to original');

    return { status: 'PASS', evidence, notes };
  },
};