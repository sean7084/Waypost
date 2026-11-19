// Item 11 - custom navigation bar on a notch device. Mocking the sync wx APIs
// (getWindowInfo / getMenuButtonBoundingClientRect) returns {} on base library
// 3.18.0, so we stub the header-metrics source (utils/header-metrics.readWindowMetrics)
// with controlled notch metrics, recompute applyHeaderMetrics, and assert the
// status-bar inset / capsule-aligned header height / drawer top. Real-device
// capsule alignment is MANUAL.
const lib = require('./lib');

async function stubMetrics(mini, metrics) {
  await mini.evaluate((m) => {
    const hm = require('utils/header-metrics');
    hm.__origRead = hm.readWindowMetrics;
    hm.readWindowMetrics = () => m;
  }, metrics);
}

async function restoreMetrics(mini) {
  await mini.evaluate(() => {
    const hm = require('utils/header-metrics');
    if (hm.__origRead) {
      hm.readWindowMetrics = hm.__origRead;
      delete hm.__origRead;
    }
  });
}

module.exports = {
  id: 11,
  key: '11-header',
  title: '刘海屏自定义导航栏 / 胶囊对齐 / 抽屉顶部',
  mode: 'mock',
  async run(mini) {
    const evidence = [];
    const notes = [];
    const chat = await lib.gotoChat(mini);
    const call = (m, ...a) => lib.callPageMethod(mini, m, ...a);

    // Notch profile: 47px status bar, 32px capsule.
    await stubMetrics(mini, {
      screenTop: 0,
      statusBarHeight: 47,
      safeAreaInsetTop: 47,
      safeAreaTop: 47,
      menuButton: { height: 32, width: 80, top: 48, bottom: 80, left: 281, right: 361 },
    });
    await call('applyHeaderMetrics');
    let inset = await chat.data('headerTopInset');
    let height = await chat.data('headerContentHeight');
    let panelTop = await chat.data('menuPanelTop');
    // headerContentHeight = ceil(32 + 6*2 + 4) = 48; inset = max(0,47) = 47;
    // menuPanelTop = 47 + 48 + 8 = 103.
    lib.assert(inset === 47, `notch headerTopInset=47 (got ${inset})`);
    lib.assert(height === 48, `notch headerContentHeight=48 capsule-aligned (got ${height})`);
    lib.assert(panelTop === 103, `notch menuPanelTop=103 (got ${panelTop})`);
    evidence.push(await lib.shot(mini, '11-notch-header'));
    notes.push(`notch metrics: statusBarHeight=47, capsule.height=32 -> headerTopInset=${inset}, headerContentHeight=${height}, menuPanelTop=${panelTop}`);

    // Drawer top aligns below the header.
    await call('toggleMenu');
    await lib.waitForData(chat, (d) => d.menuOpen, { timeout: 10000, label: 'drawer open' });
    const drawer = await chat.$('.sidebar-drawer');
    const style = await drawer.attribute('style');
    lib.assert(/padding-top:\s*103px/.test(style), `drawer padding-top follows menuPanelTop (style="${style}")`);
    evidence.push(await lib.shot(mini, '11-notch-drawer'));
    await call('closeMenu');
    notes.push(`drawer alignment: .sidebar-drawer style="${style}"`);

    // Adaptivity: a shorter status bar + smaller capsule recomputes correctly.
    await stubMetrics(mini, {
      screenTop: 0,
      statusBarHeight: 20,
      safeAreaInsetTop: 20,
      safeAreaTop: 20,
      menuButton: { height: 30, width: 78, top: 24, bottom: 54, left: 283, right: 361 },
    });
    await call('applyHeaderMetrics');
    inset = await chat.data('headerTopInset');
    height = await chat.data('headerContentHeight');
    panelTop = await chat.data('menuPanelTop');
    lib.assert(inset === 20 && height === 46 && panelTop === 74, `non-notch profile recomputes (got inset=${inset}, height=${height}, panelTop=${panelTop})`);
    notes.push(`adaptivity: statusBarHeight=20, capsule.height=30 -> inset=${inset}, height=${height}, panelTop=${panelTop}`);

    await restoreMetrics(mini);
    await call('applyHeaderMetrics');
    notes.push('MANUAL(真机): 刘海屏/胶囊按钮实际不遮挡标题、抽屉顶部对齐需真机扫码目视确认。');

    return { status: 'PASS', evidence, notes, manual: ['真机刘海屏胶囊对齐目视确认'] };
  },
};