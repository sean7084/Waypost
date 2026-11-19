// Custom navigation-bar metrics for the chat page.
//
// Port of the grafted client's src/utils/chatHeaderMetrics.js. The chat page
// draws its own header (hamburger + title) because the native navigation bar
// cannot host a drawer trigger, so it has to reproduce the status-bar inset and
// the capsule-button height itself.
function resolveChatHeaderMetrics({
  screenTop = 0,
  statusBarHeight = 0,
  safeAreaInsetTop = 0,
  safeAreaTop = 0,
  menuButton = null,
  headerTopInsetExtraPx = 0,
  headerHeightExtraPx = 0,
} = {}) {
  const resolvedScreenTop = Number(screenTop) || 0;
  const resolvedStatusBarHeight = Number(statusBarHeight || safeAreaInsetTop || safeAreaTop) || 0;
  const headerTopInsetExtra = Number(headerTopInsetExtraPx) || 0;
  const headerHeightExtra = Math.max(0, Number(headerHeightExtraPx) || 0);

  let headerContentHeight = 44 + headerHeightExtra;

  if (menuButton && menuButton.height) {
    const buttonHeight = Number(menuButton.height) || 32;
    const verticalPadding = 6;
    headerContentHeight = Math.ceil(buttonHeight + verticalPadding * 2 + headerHeightExtra);
  }

  const headerTopInset = Math.max(resolvedScreenTop, resolvedStatusBarHeight) + headerTopInsetExtra;
  const menuPanelTop = headerTopInset + headerContentHeight + 8;

  return { headerTopInset, headerContentHeight, menuPanelTop };
}

// Read the insets from the native runtime. getWindowInfo is the modern API;
// getSystemInfoSync is the fallback for older base libraries.
function readWindowMetrics() {
  const metrics = { screenTop: 0, statusBarHeight: 0, safeAreaInsetTop: 0, safeAreaTop: 0, menuButton: null };
  try {
    const info = typeof wx.getWindowInfo === 'function' ? wx.getWindowInfo() : wx.getSystemInfoSync();
    metrics.screenTop = Number((info && info.screenTop) || 0) || 0;
    metrics.statusBarHeight = Number((info && info.statusBarHeight) || 0) || 0;
    metrics.safeAreaInsetTop = Number((info && info.safeAreaInsets && info.safeAreaInsets.top) || 0) || 0;
    metrics.safeAreaTop = Number((info && info.safeArea && info.safeArea.top) || 0) || 0;
  } catch (_e) {
    metrics.statusBarHeight = 0;
  }
  try {
    metrics.menuButton =
      typeof wx.getMenuButtonBoundingClientRect === 'function' ? wx.getMenuButtonBoundingClientRect() : null;
  } catch (_e) {
    metrics.menuButton = null;
  }
  return metrics;
}

module.exports = { resolveChatHeaderMetrics, readWindowMetrics };