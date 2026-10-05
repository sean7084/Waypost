/**
 * Local runner for WeChat `miniprogram-ci` (preview + upload).
 *
 * Reads credentials from the repo-root `.env` (the same file Django loads):
 *   - WECHAT_MINI_APPID        the mini-program AppID
 *   - WECHAT_CI_PRIVATE_KEY    either a PATH to private.<appid>.key (local .env)
 *                              or the raw key CONTENTS (GitHub Actions secret)
 *
 * Usage (from the miniprogram/ folder):
 *   npm run preview                       # QR build for on-device testing
 *   npm run upload                        # push a dev version into the MP console
 *   node scripts/ci.js preview --version 1.2.0 --desc "signoff fixes"
 *   node scripts/ci.js preview --dry-run   # resolve config only; no WeChat API call
 *
 * `preview` writes ./preview-qr.jpg (git-ignored) — scan it in WeChat to try the build.
 * `upload`  pushes a development version (版本管理 → 开发版本) to submit for review.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const MINIPROGRAM_ROOT = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(MINIPROGRAM_ROOT, '..');
const ENV_PATH = path.join(REPO_ROOT, '.env');

// Parse a repo-root .env the same way waypost/runtime_setup.load_local_env does:
// split on the first '=', skip blanks/comments, strip one layer of surrounding quotes.
function loadDotEnv(file) {
  const out = {};
  if (!fs.existsSync(file)) {
    return out;
  }
  const text = fs.readFileSync(file, 'utf8');
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) {
      continue;
    }
    const idx = line.indexOf('=');
    const key = line.slice(0, idx).trim();
    if (!key) {
      continue;
    }
    let value = line.slice(idx + 1).trim();
    value = value.replace(/^"(.*)"$/, '$1').replace(/^'(.*)'$/, '$1');
    out[key] = value;
  }
  return out;
}

// Precedence: real environment variable > .env file value.
function readConfig(key, dotEnv) {
  const fromProcess = process.env[key];
  if (fromProcess && fromProcess.trim()) {
    return fromProcess.trim();
  }
  return (dotEnv[key] || '').trim();
}

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token.startsWith('--')) {
      const name = token.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) {
        args[name] = next;
        i += 1;
      } else {
        args[name] = true;
      }
    } else {
      args._.push(token);
    }
  }
  return args;
}

// Resolve WECHAT_CI_PRIVATE_KEY to a real private-key file path. Accepts either a
// path (local .env convention) or the raw key contents (GitHub Actions secret).
function resolvePrivateKey(value) {
  if (!value) {
    throw new Error(
      'WECHAT_CI_PRIVATE_KEY is not set. Add it to .env as the path to ' +
      'private.<appid>.key, or set the repo secret to the key contents.'
    );
  }
  const candidates = [REPO_ROOT, process.cwd(), MINIPROGRAM_ROOT].map((base) => (
    path.isAbsolute(value) ? value : path.join(base, value)
  ));
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  if (value.includes('-----BEGIN')) {
    const tmp = path.join(os.tmpdir(), `miniprogram-ci-${process.pid}.key`);
    const body = value.endsWith('\n') ? value : `${value}\n`;
    fs.writeFileSync(tmp, body, { mode: 0o600 });
    return tmp;
  }
  throw new Error(
    `WECHAT_CI_PRIVATE_KEY does not point to an existing file and is not inline ` +
    `key contents: ${value}`
  );
}

function readPackageVersion() {
  try {
    return require('../package.json').version || '1.0.0';
  } catch (err) {
    return '1.0.0';
  }
}

async function main() {
  let ci;
  try {
    ci = require('miniprogram-ci');
  } catch (err) {
    console.error('miniprogram-ci is not installed. Run this first (from miniprogram/):');
    console.error('    npm install');
    process.exit(2);
    return;
  }

  const dotEnv = loadDotEnv(ENV_PATH);
  const args = parseArgs(process.argv.slice(2));
  const command = String(args._[0] || 'preview').toLowerCase();

  if (!['preview', 'upload'].includes(command)) {
    console.error(`Unknown command "${command}". Use: preview | upload`);
    process.exit(2);
    return;
  }

  const appid = readConfig('WECHAT_MINI_APPID', dotEnv);
  if (!appid) {
    console.error('WECHAT_MINI_APPID is not set (checked environment and .env).');
    process.exit(2);
    return;
  }

  const privateKeyPath = resolvePrivateKey(readConfig('WECHAT_CI_PRIVATE_KEY', dotEnv));
  const version = args.version && args.version !== true ? String(args.version) : readPackageVersion();
  const who = os.userInfo().username;
  const day = new Date().toISOString().slice(0, 10);
  const defaultDesc = `${command} by ${who} on ${day}`;
  const desc = args.desc && args.desc !== true ? String(args.desc) : defaultDesc;

  const project = new ci.Project({
    appid,
    type: 'miniProgram',
    projectPath: MINIPROGRAM_ROOT,
    privateKeyPath,
    // Node-only tooling that must never be packed into the mini program bundle
    // (mirrors packOptions.ignore in project.config.json).
    ignores: [
      'node_modules/**/*',
      'scripts/**/*',
      'package.json',
      'package-lock.json',
      'eslint.config.js',
      'README.md',
      'preview-qr.jpg',
    ],
  });

  const setting = {
    es6: true,
    es7: true,
    minify: true,
    minifyWXML: true,
    minifyWXSS: true,
    autoPrefixWXSS: true,
  };

  const onProgressUpdate = () => {
    // no-op: keep the console output readable during local runs
  };

  console.log(`\n[miniprogram-ci] ${command}  appid=${appid}  version=${version}`);
  console.log(`[miniprogram-ci] project=${MINIPROGRAM_ROOT}`);
  console.log(`[miniprogram-ci] key=${privateKeyPath}\n`);

  if (args['dry-run']) {
    console.log('[dry-run] config resolved OK (appid + private key found); skipping the WeChat API call.');
    return;
  }

  if (command === 'preview') {
    const qrcodeOutputDest = path.join(MINIPROGRAM_ROOT, 'preview-qr.jpg');
    await ci.preview({
      project,
      version,
      desc,
      setting,
      qrcodeFormat: 'image',
      qrcodeOutputDest,
      onProgressUpdate,
    });
    console.log(`\n[OK] Preview build ready. Scan the QR code with WeChat:`);
    console.log(`     ${qrcodeOutputDest}`);
  } else {
    await ci.upload({
      project,
      version,
      desc,
      setting,
      onProgressUpdate,
    });
    console.log(`\n[OK] Uploaded development version ${version} to the WeChat MP console.`);
    console.log('     版本管理 → 开发版本 → 提交审核 when you are ready to release.');
  }
}

main().catch((err) => {
  const cmd = String(process.argv[2] || 'preview').toLowerCase();
  const msg = err && err.message ? String(err.message) : String(err);
  console.error(`\n[FAIL] miniprogram-ci ${cmd} failed:`);
  console.error(msg);

  // WeChat errCode -10008 "invalid ip": the MP console has an upload IP-whitelist
  // enabled and this machine's public IP is not on it. Most common first-run blocker.
  const ipMatch = msg.match(/invalid ip:\s*([0-9a-fA-F:.]+)/);
  if (ipMatch || /-10008|whitelist|白名单/i.test(msg)) {
    console.error('\n--- IP whitelist (WeChat errCode -10008) ---');
    if (ipMatch) {
      console.error(`Detected source IP: ${ipMatch[1]}`);
    }
    console.error('The MP console has 小程序代码上传 → IP白名单 enabled. Fix ONE of:');
    console.error('  1) Turn the IP whitelist OFF — recommended for dev machines AND for');
    console.error('     GitHub Actions, whose runner IPs rotate on every run; or');
    console.error('  2) Add the IP above at mp.weixin.qq.com → 开发管理 → 开发设置 →');
    console.error('     小程序代码上传. IPv6 / home broadband IPs often change, so this');
    console.error('     can silently break on a later run.');
    console.error(`After changing the setting, re-run:  npm run ${cmd === 'upload' ? 'upload' : 'preview'}`);
  }
  process.exit(1);
});
