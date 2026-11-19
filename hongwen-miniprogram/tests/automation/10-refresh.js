// Item 10 - access-token expiry: a 401 triggers one refresh-and-retry with a valid
// refresh token; an invalid refresh token clears the session and reLaunches to
// /pages/index/index?loggedOut=1. Fully automatic against real Django + SimpleJWT.
const lib = require('./lib');

const EXPIRED = 'expired.access.token';

module.exports = {
  id: 10,
  key: '10-refresh',
  title: '401 刷新（有效 refresh 重试 / 无效 refresh 回登录页）',
  mode: 'auto',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    const snap = await lib.storageSnapshot(mini);
    lib.assert(snap.refresh_token, 'a valid refresh token exists after login');
    evidence.push(await lib.shot(mini, '10-chat-before'));

    // --- valid refresh: corrupt the access token, keep the refresh token.
    await mini.evaluate((t) => wx.setStorageSync('auth_token', t), EXPIRED);
    await lib.callPageMethod(mini, 'refreshSessionSummaries', {});
    await lib.waitForData(chat, (d) => d.sessionsLoading === false && d.sessionsError === '', {
      timeout: 20000,
      label: 'refresh-and-retry succeeds',
    });
    const afterRefresh = await lib.storageSnapshot(mini);
    lib.assert(afterRefresh.auth_token && afterRefresh.auth_token !== EXPIRED, 'access token renewed (not the expired one)');
    lib.assert(afterRefresh.refresh_token === snap.refresh_token, 'refresh token unchanged (ROTATE_REFRESH_TOKENS=False)');
    const path1 = await lib.currentPagePath(mini);
    lib.assert(path1 === 'pages/chat/chat', 'still on chat after successful refresh');
    notes.push(`valid refresh: 401 -> /api/v1/auth/token/refresh/ 200 -> new access stored, request retried OK, stayed on chat`);
    evidence.push(await lib.shot(mini, '10-refresh-ok'));

    // --- invalid refresh: corrupt both tokens -> clear + reLaunch loggedOut.
    await mini.evaluate((t) => {
      wx.setStorageSync('auth_token', t);
      wx.setStorageSync('refresh_token', 'invalid.refresh.token');
    }, EXPIRED);
    await lib.callPageMethod(mini, 'refreshSessionSummaries', {});
    const indexPage = await lib.waitFor(
      async () => {
        const cur = await mini.currentPage();
        return cur && cur.path === 'pages/index/index' ? cur : null;
      },
      { timeout: 20000, label: 'reLaunch to login page' }
    );
    lib.assert(indexPage.query && indexPage.query.loggedOut === '1', `login page query loggedOut=1 (got ${JSON.stringify(indexPage.query)})`);
    const cleared = await lib.storageSnapshot(mini);
    lib.assert(!cleared.auth_token && !cleared.refresh_token, 'tokens cleared after failed refresh');
    const prompt = await indexPage.data('loginPrompt');
    evidence.push(await lib.shot(mini, '10-logged-out'));
    notes.push(`invalid refresh: 401 -> refresh fails -> clearTokens + reLaunch '?loggedOut=1', prompt="${prompt}"`);

    return { status: 'PASS', evidence, notes };
  },
};