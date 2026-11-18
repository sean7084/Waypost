import { describe, it, expect } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { resolveChatHeaderMetrics } = require('./header-metrics.js');

// Ported from the grafted client's src/utils/chatHeaderMetrics.test.js.
describe('resolveChatHeaderMetrics', () => {
  it('uses the larger top inset source and applies upward tuning', () => {
    expect(
      resolveChatHeaderMetrics({
        screenTop: 98,
        statusBarHeight: 47,
        headerTopInsetExtraPx: -48,
        headerHeightExtraPx: 4,
        menuButton: { height: 32 },
      })
    ).toEqual({ headerTopInset: 50, headerContentHeight: 48, menuPanelTop: 106 });
  });

  it('falls back to safe area top when status bar height is unavailable', () => {
    expect(resolveChatHeaderMetrics({ safeAreaTop: 47, headerHeightExtraPx: 0 })).toEqual({
      headerTopInset: 47,
      headerContentHeight: 44,
      menuPanelTop: 99,
    });
  });

  it('does not allow negative header height extras', () => {
    expect(
      resolveChatHeaderMetrics({ screenTop: 20, headerHeightExtraPx: -12, menuButton: { height: 32 } })
        .headerContentHeight
    ).toBe(44);
  });

  it('falls back to safeAreaInsets.top before safeArea.top', () => {
    expect(resolveChatHeaderMetrics({ safeAreaInsetTop: 44, safeAreaTop: 20 }).headerTopInset).toBe(44);
  });

  it('defaults to a 44px header with no capsule measurement', () => {
    expect(resolveChatHeaderMetrics()).toEqual({ headerTopInset: 0, headerContentHeight: 44, menuPanelTop: 52 });
  });
});