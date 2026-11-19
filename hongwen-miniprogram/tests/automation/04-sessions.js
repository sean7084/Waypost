// Item 4 - session drawer CRUD: new / select (callMethod + UI tap) / rename
// (editable modal) / delete-current -> automatic re-bootstrap. Fully automatic.
const lib = require('./lib');

async function tapSessionItem(chat, sessionId) {
  const items = await chat.$$('.session-item-main');
  for (const item of items) {
    const id = await item.attribute('data-session-id');
    if (id === sessionId) {
      await item.tap();
      return true;
    }
  }
  return false;
}

module.exports = {
  id: 4,
  key: '04-sessions',
  title: '会话抽屉 CRUD（新建/切换/重命名/删除当前自动 bootstrap）',
  mode: 'auto',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    const call = (m, ...a) => lib.callPageMethod(mini, m, ...a);

    const idA = await chat.data('currentSessionId');
    lib.assert(idA, 'initial bootstrap session exists');

    // new
    await call('handleCreateNewSession');
    await lib.waitForData(chat, (d) => d.currentSessionId && d.currentSessionId !== idA, {
      timeout: 15000,
      label: 'force_new session',
    });
    const idB = await chat.data('currentSessionId');
    notes.push(`new: force_new created session ${idB} (previous ${idA})`);

    // open drawer, list renders
    await call('toggleMenu');
    await lib.waitForData(chat, (d) => d.menuOpen && d.sessionSummaries.length >= 2, {
      timeout: 15000,
      label: 'drawer with >=2 sessions',
    });
    evidence.push(await lib.shot(mini, '04-drawer-list'));

    // select via real UI tap on .session-item-main
    const tapped = await tapSessionItem(chat, idA);
    lib.assert(tapped, `found .session-item-main for ${idA}`);
    await lib.waitForData(chat, (d) => d.currentSessionId === idA, { timeout: 15000, label: 'select idA via tap' });
    notes.push(`select: tapped .session-item-main -> currentSessionId=${idA}`);

    // rename via editable modal (mock returns confirm + content)
    await mini.mockWxMethod('showModal', function () {
      return { confirm: true, content: '自动化重命名会话', errMsg: 'showModal:ok' };
    });
    await call('renameSession', { session_id: idA, title: '原标题' });
    await lib.waitForData(
      chat,
      (d) => (d.sessionSummaries || []).some((s) => s.session_id === idA && s.title === '自动化重命名会话'),
      { timeout: 15000, label: 'rename reflected in sessionSummaries' }
    );
    await mini.restoreWxMethod('showModal');
    notes.push('rename: editable modal -> PATCH title="自动化重命名会话" reflected in drawer');
    await call('toggleMenu');
    await lib.waitForData(chat, (d) => d.menuOpen, { timeout: 10000, label: 'drawer reopen' });
    evidence.push(await lib.shot(mini, '04-drawer-renamed'));
    await call('closeMenu');

    // delete the CURRENT session -> backend returns bootstrap -> currentSessionId changes
    await mini.mockWxMethod('showModal', function () {
      return { confirm: true, errMsg: 'showModal:ok' };
    });
    await call('deleteSession', { session_id: idA });
    await lib.waitForData(chat, (d) => d.currentSessionId && d.currentSessionId !== idA, {
      timeout: 15000,
      label: 'auto re-bootstrap after deleting current session',
    });
    await mini.restoreWxMethod('showModal');
    const after = await chat.data('sessionSummaries');
    lib.assert(!after.some((s) => s.session_id === idA), 'deleted session removed from summaries');
    const newCurrent = await chat.data('currentSessionId');
    evidence.push(await lib.shot(mini, '04-after-delete-bootstrap'));
    notes.push(`delete-current: deleted ${idA} -> auto bootstrap currentSessionId=${newCurrent}, removed from list`);

    return { status: 'PASS', evidence, notes };
  },
};