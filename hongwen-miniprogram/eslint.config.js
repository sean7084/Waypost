// ESLint flat config for the 泓问 native WeChat mini program.
//
// Three surfaces, three sets of globals (mirrors miniprogram/eslint.config.js):
//   * mini program runtime  - CommonJS + wx/App/Page/Component
//   * scripts/              - local Node tooling (miniprogram-ci runner)
//   * *.test.js             - vitest suites, ESM, Node globals
const js = require('@eslint/js');

const SHARED_RULES = {
  'no-unused-vars': ['warn', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
  'no-empty': ['error', { allowEmptyCatch: true }],
};

const COMMONJS_GLOBALS = {
  require: 'readonly',
  module: 'writable',
  exports: 'writable',
  console: 'readonly',
  setTimeout: 'readonly',
  clearTimeout: 'readonly',
  setInterval: 'readonly',
  clearInterval: 'readonly',
  Promise: 'readonly',
};

module.exports = [
  { ignores: ['node_modules/**', 'coverage/**'] },
  js.configs.recommended,
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: 'commonjs',
      globals: {
        ...COMMONJS_GLOBALS,
        // WeChat mini program runtime globals
        wx: 'readonly',
        App: 'readonly',
        Page: 'readonly',
        Component: 'readonly',
        Behavior: 'readonly',
        getApp: 'readonly',
        getCurrentPages: 'readonly',
        requirePlugin: 'readonly',
      },
    },
    rules: SHARED_RULES,
  },
  {
    // Local Node tooling (miniprogram-ci runner) - never packed into the bundle.
    files: ['scripts/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'commonjs',
      globals: {
        ...COMMONJS_GLOBALS,
        process: 'readonly',
        __dirname: 'readonly',
        __filename: 'readonly',
        Buffer: 'readonly',
      },
    },
    rules: SHARED_RULES,
  },
  {
    // DevTools automator harness: Node tooling that also references mini program
    // globals (wx / getCurrentPages) inside automator evaluate() callbacks.
    files: ['tests/automation/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'commonjs',
      globals: {
        ...COMMONJS_GLOBALS,
        process: 'readonly',
        __dirname: 'readonly',
        __filename: 'readonly',
        Buffer: 'readonly',
        wx: 'readonly',
        getApp: 'readonly',
        getCurrentPages: 'readonly',
      },
    },
    rules: SHARED_RULES,
  },
  {
    // vitest suites load the CommonJS utils through node:module createRequire,
    // so they are ESM files with Node globals rather than mini program globals.
    files: ['**/*.test.js', '**/*.mjs'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...COMMONJS_GLOBALS,
        process: 'readonly',
        Buffer: 'readonly',
        globalThis: 'readonly',
        URL: 'readonly',
        TextEncoder: 'readonly',
        Uint8Array: 'readonly',
        ArrayBuffer: 'readonly',
      },
    },
    rules: SHARED_RULES,
  },
];