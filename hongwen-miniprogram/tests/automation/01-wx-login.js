// Item 1 - wx.login -> /auth/wx_login: new user shows the authorize button,
// old user redirects straight into chat. Fully automatic against real Django.
const lib = require('./lib');

module.exports = {
  id: 1,
  key: '01-wx-login',
  title: 'wx.login 新/老用户登录',
  mode: 'auto',
  async run(mini) {
    const evidence = [];
    const notes = [];

    // --- new user: remove the simulator's customer so wx_login answers new_user
    lib.fixture('reset-user');
    await lib.clearStorage(mini);
    const index = await mini.reLaunch('/pages/index/index');
    await lib.waitForData(index, (d) => d.isNewUser === true && d.loading === false, {
      timeout: 30000,
      label: 'new_user authorize state',
    });
    const btn = await index.$('.btn');
    lib.assert(btn, 'new-user authorize button rendered');
    const label = await btn.text();
    lib.assert(label.includes('授权'), `authorize button label (got "${label}")`);
    evidence.push(await lib.shot(mini, '01-new-user'));
    notes.push(`new_user: data.isNewUser=true, button "${label.trim()}" present`);

    // Complete authorization to establish a session (also pre-exercises item 2).
    await lib.mockUserProfile(mini, '泓问自动化用户', 'https://waypost.istore-tech.cn/static/avatar.png');
    await lib.callPageMethod(mini, 'handleAuthorize');
    const chat = await lib.waitFor(
      async () => {
        const c = await mini.currentPage();
        return c && c.path === 'pages/chat/chat' ? c : null;
      },
      { timeout: 30000, label: 'redirect to chat after authorize' }
    );
    await lib.waitForData(chat, (d) => Boolean(d.currentSessionId), { timeout: 20000, label: 'chat bootstrap' });

    // --- old user: clear tokens, wx_login now answers status ok -> redirect chat
    await lib.clearStorage(mini);
    await mini.reLaunch('/pages/index/index');
    const chat2 = await lib.waitFor(
      async () => {
        const c = await mini.currentPage();
        return c && c.path === 'pages/chat/chat' ? c : null;
      },
      { timeout: 30000, label: 'old-user redirect to chat' }
    );
    await lib.waitForData(chat2, (d) => Boolean(d.currentSessionId), { timeout: 20000, label: 'old-user bootstrap' });
    const sessionId = await chat2.data('currentSessionId');
    evidence.push(await lib.shot(mini, '01-old-user-chat'));
    notes.push(`old_user: wx_login status ok -> redirectTo /pages/chat/chat, bootstrap session_id=${sessionId}`);

    return { status: 'PASS', evidence, notes };
  },
};