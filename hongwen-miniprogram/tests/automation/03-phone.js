// Item 3 - phone-number authorization banner (open-type=getPhoneNumber -> /auth/get-phone).
// The native authorization popup cannot be driven, so we (a) exercise the real
// backend branch with a fake code (graceful failure) and (b) mock the network to
// verify the client success branch. Real-device popup is MANUAL.
const lib = require('./lib');

module.exports = {
  id: 3,
  key: '03-phone',
  title: '手机号授权横幅 (getPhoneNumber -> get-phone)',
  mode: 'mock',
  async run(mini) {
    const evidence = [];
    const notes = [];

    await mini.evaluate(() => wx.removeStorageSync('has_phone'));
    const chat = await lib.gotoChat(mini);
    await chat.setData({ hasPhone: false });
    const btn = await chat.$('.phone-btn');
    lib.assert(btn, 'phone authorization banner rendered when hasPhone=false');
    const openType = await btn.attribute('open-type');
    lib.assert(openType === 'getPhoneNumber', `button open-type=getPhoneNumber (got ${openType})`);
    evidence.push(await lib.shot(mini, '03-phone-banner'));

    // (a) Real backend branch: a fake code is refused by WeChat getuserphonenumber.
    const backend = await mini.evaluate(async () => {
      const api = require('utils/api');
      try {
        const ok = await api.getPhone({ code: 'automator-fake-phone-code' });
        return { unexpectedSuccess: ok };
      } catch (e) {
        return { statusCode: e.statusCode || 0, detail: (e.data && e.data.detail) || e.message };
      }
    });
    lib.assert(
      backend.statusCode === 400 || backend.statusCode === 500,
      `backend get-phone refuses fake code (got ${JSON.stringify(backend)})`
    );
    notes.push(`real backend branch: /auth/get-phone fake code -> HTTP ${backend.statusCode} "${backend.detail}"`);

    // The page handler must swallow the failure and keep hasPhone=false.
    await lib.callPageMethod(mini, 'onGetPhoneNumber', { detail: { errMsg: 'getPhoneNumber:ok', code: 'automator-fake-phone-code' } });
    await lib.sleep(1500);
    let hasPhone = await chat.data('hasPhone');
    lib.assert(hasPhone === false, 'hasPhone stays false after backend refusal');

    // (b) Mock success branch: intercept the get-phone request only.
    await mini.mockWxMethod('request', function (opts) {
      if (String(opts.url).indexOf('/auth/get-phone') >= 0) {
        return { statusCode: 200, data: { status: 'success', phone: '13800138000' } };
      }
      return { statusCode: 200, data: {} };
    });
    await lib.callPageMethod(mini, 'onGetPhoneNumber', { detail: { errMsg: 'getPhoneNumber:ok', code: 'mock-ok-code' } });
    await lib.waitForData(chat, (d) => d.hasPhone === true, { timeout: 10000, label: 'hasPhone=true after mock success' });
    const stored = await mini.evaluate(() => wx.getStorageSync('has_phone'));
    lib.assert(stored === true, 'has_phone persisted to storage');
    await mini.restoreWxMethod('request');
    const btnAfter = await chat.$('.phone-btn');
    lib.assert(!btnAfter, 'phone banner hidden once hasPhone=true');
    evidence.push(await lib.shot(mini, '03-phone-success'));
    notes.push('mock success branch: getPhoneNumber:ok -> get-phone 200 -> hasPhone=true, has_phone stored, banner hidden');
    notes.push('MANUAL(真机): 原生手机号授权弹窗不可驱动，需真机扫码点击横幅验证真实 code 换取手机号。');

    return { status: 'PASS', evidence, notes, manual: ['真机原生手机号授权弹窗 -> 真实 get-phone 换号'] };
  },
};