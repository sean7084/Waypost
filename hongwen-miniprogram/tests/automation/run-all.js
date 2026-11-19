// Orchestrator for the 11-item DevTools automator harness.
//
//   node tests/automation/run-all.js            # run all 11 items
//   node tests/automation/run-all.js 05 08      # run a subset by key prefix
//
// Starts/reuses the harness Django server (:8001, Volcengine embed override),
// seeds triage playbooks, then runs each item over its own automator connection
// (one connection at a time). Writes RESULTS.md + screenshots and attempts a
// preview QR for the real-device MANUAL items. Exit code 1 if any item FAILs.
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const lib = require('./lib');
const { ensureServer } = require('./ensure-server');

const ITEMS = [
  require('./01-wx-login'),
  require('./02-profile'),
  require('./03-phone'),
  require('./04-sessions'),
  require('./05-stream'),
  require('./06-voice'),
  require('./07-tts'),
  require('./08-regenerate'),
  require('./09-image'),
  require('./10-refresh'),
  require('./11-header'),
];

const MINI_DIR = path.resolve(__dirname, '..', '..');
const REPORT = path.join(__dirname, 'RESULTS.md');

function log(msg) {
  console.log(msg);
}

async function runItem(item) {
  let mini;
  const started = Date.now();
  try {
    mini = await lib.connect();
    await lib.applyEnv(mini);
    const result = await item.run(mini);
    return { ...result, ms: Date.now() - started };
  } catch (e) {
    return { status: 'FAIL', error: e.message, stack: e.stack, evidence: [], notes: [], ms: Date.now() - started };
  } finally {
    if (mini) {
      try {
        await mini.disconnect();
      } catch (_e) {
        /* already disposed */
      }
    }
  }
}

function attemptPreview() {
  const qr = path.join(MINI_DIR, 'preview-qr.jpg');
  try {
    const res = spawnSync('npm', ['run', 'preview'], {
      cwd: MINI_DIR,
      encoding: 'utf-8',
      timeout: 240000,
      shell: true,
    });
    const exists = fs.existsSync(qr);
    return {
      ok: exists,
      detail: exists ? 'preview-qr.jpg generated' : `preview failed (status ${res.status}): ${String(res.stderr || res.stdout || '').slice(-400)}`,
    };
  } catch (e) {
    return { ok: false, detail: `preview error: ${e.message}` };
  }
}

function buildReport(results, preview, seedInfo) {
  const now = new Date().toISOString();
  const counts = results.reduce((acc, r) => {
    acc[r.result.status] = (acc[r.result.status] || 0) + 1;
    return acc;
  }, {});
  const lines = [];
  lines.push('# 泓问小程序 DevTools Automator 运行时测试报告');
  lines.push('');
  lines.push(`- 生成时间：${now}`);
  lines.push(`- AppID：wxd152caad9f9c76ef（品牌「泓问」）`);
  lines.push(`- 自动化端口：${lib.WS_ENDPOINT}（WeChat DevTools，platform=devtools）`);
  lines.push(`- 后端：harness 自有 Django 服务 ${lib.ORIGIN}（共享 dev SQLite 与 SECRET_KEY）`);
  lines.push(`- 汇总：PASS ${counts.PASS || 0} / FAIL ${counts.FAIL || 0} / MANUAL ${counts.MANUAL || 0}（共 ${results.length} 项）`);
  lines.push('');
  lines.push('## 环境与 provider 实测说明');
  lines.push('');
  lines.push('- **Qwen/DashScope 账户欠费（Arrearage）**：primary embed（text-embedding-v4）、primary TTS（cosyvoice-v3-flash）、primary ASR（qwen3-asr-flash）调用返回 HTTP 400 Arrearage。');
  lines.push('- **Volcengine 正常**：primary text/omni/vision（doubao-seed-2-0-lite-260428）流式聊天实测打通；backup embed（doubao-embedding-vision-251215）用于 triage 种子与匹配；TTS 自动回退到 volcengine/seed-tts-2.0 实测合成成功。');
  lines.push(`- 因 primary embed 欠费且用户的 :8000 服务不可重启，harness 另起 :8001 服务并以进程级 \`PRIMARY_EMBED_MODEL=${lib.EMBED_OVERRIDE}\` 覆盖（**不修改仓库 .env / config/env.js**），客户端经 automator \`evaluate\` 运行时把 API origin 指向 :8001。`);
  lines.push(`- triage 种子：${seedInfo ? `playbooks=${JSON.stringify(seedInfo.playbooks)}, steps=${seedInfo.steps}, customer_id=${seedInfo.customer_id}` : '见 fixtures.py ensure-seed'}。`);
  lines.push('- 流式必须连真 Django：`mockWxMethod(\'request\')` 无法伪造 enableChunked 分帧，项 5/8 的打字机与重答均走真实 SSE。');
  lines.push('');
  lines.push('## 逐项结果');
  lines.push('');
  lines.push('| # | 项目 | 方式 | 状态 |');
  lines.push('| --- | --- | --- | --- |');
  for (const r of results) {
    lines.push(`| ${r.item.id} | ${r.item.title} | ${r.item.mode} | **${r.result.status}** |`);
  }
  lines.push('');
  for (const r of results) {
    lines.push(`### ${r.item.id}. ${r.item.title} — ${r.result.status}`);
    lines.push('');
    lines.push(`- 方式：${r.item.mode} 耗时：${r.result.ms}ms`);
    if (r.result.error) {
      lines.push(`- 错误：\`${r.result.error}\``);
      if (r.result.stack) lines.push('```', String(r.result.stack).split('\n').slice(0, 6).join('\n'), '```');
    }
    if (r.result.notes && r.result.notes.length) {
      lines.push('- 断言/证据：');
      for (const n of r.result.notes) lines.push(`  - ${n}`);
    }
    if (r.result.evidence && r.result.evidence.length) {
      lines.push('- 截图：');
      for (const e of r.result.evidence) lines.push(`  - ![${path.basename(e)}](${e})`);
    }
    if (r.result.manual && r.result.manual.length) {
      lines.push('- 真机手测：');
      for (const m of r.result.manual) lines.push(`  - [ ] ${m}`);
    }
    lines.push('');
  }
  lines.push('## 真机手测清单（preview QR）');
  lines.push('');
  lines.push(`- preview QR：${preview.ok ? '`hongwen-miniprogram/preview-qr.jpg`（已生成，微信扫码）' : `未生成 — ${preview.detail}`}`);
  lines.push('- 生成命令：`cd hongwen-miniprogram && npm run preview`（需 CI 私钥 + 公网 IP 白名单）。');
  lines.push('- 需真机配合的项：');
  lines.push('  - [ ] 项 2：getUserProfile 授权弹窗 + 2022-10-25 起匿名资料展示（预期）。');
  lines.push('  - [ ] 项 3：原生手机号授权弹窗 → 真实 code 换取手机号。');
  lines.push('  - [ ] 项 6：录音电平条随音量起伏、取消/发送两条路径、转写回填、自动朗读。');
  lines.push('  - [ ] 项 7：TTS 两条路径实际可听、再次点击停止。');
  lines.push('  - [ ] 项 9：相册/拍照真实入口与系统压缩 <=15MB。');
  lines.push('  - [ ] 项 11：刘海屏胶囊按钮不遮挡标题、抽屉顶部对齐目视确认。');
  lines.push('');
  lines.push('## 复跑');
  lines.push('');
  lines.push('```bash');
  lines.push('cd hongwen-miniprogram');
  lines.push('node tests/automation/run-all.js          # 全部 11 项');
  lines.push('node tests/automation/run-all.js 05 08    # 子集');
  lines.push('npm test                                  # vitest 纯逻辑仍须绿');
  lines.push('```');
  lines.push('');
  return lines.join('\n');
}

async function main() {
  const subset = process.argv.slice(2);
  const items = subset.length ? ITEMS.filter((i) => subset.some((s) => i.key.startsWith(s) || String(i.id) === s)) : ITEMS;

  log('[run-all] ensuring harness server...');
  await ensureServer();
  log('[run-all] seeding triage playbooks + test customer...');
  const seedInfo = lib.fixture('ensure-seed');
  log(`[run-all] seed: ${JSON.stringify(seedInfo)}`);

  const results = [];
  for (const item of items) {
    log(`\n[run-all] === item ${item.id} (${item.key}) ${item.title} [${item.mode}] ===`);
    const result = await runItem(item);
    log(`[run-all] item ${item.id} -> ${result.status}${result.error ? `: ${result.error}` : ''}`);
    results.push({ item, result });
    await lib.sleep(500);
  }

  log('\n[run-all] attempting preview QR...');
  const preview = process.env.HARNESS_SKIP_PREVIEW
    ? { ok: fs.existsSync(path.join(MINI_DIR, 'preview-qr.jpg')), detail: 'skipped (HARNESS_SKIP_PREVIEW)' }
    : attemptPreview();
  log(`[run-all] preview: ${preview.detail}`);

  // Leave the DevTools client pointing back at the user's normal :8000 server.
  try {
    const mini = await lib.connect();
    await lib.restoreEnv(mini);
    await mini.disconnect();
    log('[run-all] client API origin restored to :8000');
  } catch (e) {
    log(`[run-all] env restore skipped: ${e.message}`);
  }

  fs.writeFileSync(REPORT, buildReport(results, preview, seedInfo), 'utf-8');
  log(`\n[run-all] report written: ${REPORT}`);

  log('\n==== SUMMARY ====');
  for (const r of results) log(`  ${r.item.id}. ${r.result.status.padEnd(6)} ${r.item.title}`);
  const failed = results.filter((r) => r.result.status === 'FAIL');
  log(`\nPASS=${results.filter((r) => r.result.status === 'PASS').length} FAIL=${failed.length} MANUAL=${results.filter((r) => r.result.status === 'MANUAL').length}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error('[run-all] fatal', e);
  process.exit(1);
});