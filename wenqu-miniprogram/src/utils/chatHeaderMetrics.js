export function resolveChatHeaderMetrics({
  screenTop = 0,
  statusBarHeight = 0,
  safeAreaInsetTop = 0,
  safeAreaTop = 0,
  menuButton = null,
  headerTopInsetExtraPx = 0,
  headerHeightExtraPx = 0
} = {}) {
  const resolvedScreenTop = Number(screenTop) || 0
  const resolvedStatusBarHeight = Number(statusBarHeight || safeAreaInsetTop || safeAreaTop) || 0
  const headerTopInsetExtra = Number(headerTopInsetExtraPx) || 0
  const headerHeightExtra = Math.max(0, Number(headerHeightExtraPx) || 0)

  let headerContentHeight = 44 + headerHeightExtra

  if (menuButton && menuButton.height) {
    const buttonHeight = Number(menuButton.height) || 32
    const verticalPadding = 6
    headerContentHeight = Math.ceil(buttonHeight + (verticalPadding * 2) + headerHeightExtra)
  }

  const headerTopInset = Math.max(resolvedScreenTop, resolvedStatusBarHeight) + headerTopInsetExtra
  const menuPanelTop = headerTopInset + headerContentHeight + 8

  return {
    headerTopInset,
    headerContentHeight,
    menuPanelTop
  }
}
