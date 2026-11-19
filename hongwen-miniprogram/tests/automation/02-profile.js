// Item 2 - avatar/nickname authorization: wx.getUserProfile -> /auth/update_wx_user.
// The native profile popup is mocked (semi-automatic); we assert the backend
// call persists the nickname. Real-device anonymous-profile behaviour is MANUAL.
const lib = require('./lib');

const NICKNAME = '泓问头像昵称测试';
const AVATAR = 'https://waypost.istore-tech.cn/static/test-avatar.png';

module.exports = {
  id: 2,
  key: '02-profile',
  title: '头像昵称授权 (getUserProfile -> update_wx_user)',
  mode: 'mock',
  async run(mini) {
    const evidence = [];
    const notes = [];

    lib.fixture('reset-user');
    await lib.clearStorage(mini);
    const index = await mini.reLaunch('/pages/index/index');
    await lib.waitForData(index, (d) => d.isNewUser === true, { timeout: 30000, label: 'new_user state' });
    evidence.push(await lib.shot(mini, '02-authorize-button'));

    await lib.mockUserProfile(mini, NICKNAME, AVATAR);
    await lib.callPageMethod(mini, 'handleAuthorize');
    const chat = await lib.waitFor(
      async () => {
        const c = await mini.currentPage();
        return c && c.path === 'pages/chat/chat' ? c : null;
      },
      { timeout: 30000, label: 'redirect to chat after update_wx_user' }
    );
    await lib.waitForData(chat, (d) => Boolean(d.currentSessionId), { timeout: 20000, label: 'chat bootstrap' });

    const snap = await lib.storageSnapshot(mini);
    lib.assert(snap.nickname === NICKNAME, `nickname persisted to storage (got "${snap.nickname}")`);
    const profile = lib.fixture('get-profile', snap.external_user_id);
    lib.assert(profile.nickname === NICKNAME, `nickname persisted to DB (got "${profile.nickname}")`);
    evidence.push(await lib.shot(mini, '02-chat-after-profile'));
    notes.push(`update_wx_user persisted nickname="${NICKNAME}" to storage and HongwenProfile.nickname`);
    notes.push('MANUAL(真机): 微信自 2022-10-25 起 getUserProfile 返回匿名资料(微信用户/灰色头像)，属预期；真机扫码验证授权弹窗与匿名资料展示。');

    await mini.restoreWxMethod('getUserProfile');
    return { status: 'PASS', evidence, notes, manual: ['真机授权弹窗 + 匿名资料展示（2022-10-25 起预期）'] };
  },
};