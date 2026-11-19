import { defineConfig } from 'vitest/config';

// The utils are CommonJS mini program modules; the suites load them through
// node:module createRequire so the same file runs in WeChat and under vitest.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['utils/**/*.test.js'],
    globals: false,
  },
});