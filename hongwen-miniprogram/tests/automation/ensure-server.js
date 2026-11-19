// Ensure the harness-owned Django server is listening on :8001 with the
// Volcengine embed override. Reuses an already-running server; otherwise spawns
// a detached `manage.py runserver` and waits for /healthz. The user's :8000
// server is never touched.
const http = require('http');
const { spawn } = require('child_process');
const { ORIGIN, PORT, REPO_ROOT, PYTHON, EMBED_OVERRIDE, sleep } = require('./lib');

function probe() {
  return new Promise((resolve) => {
    const req = http.get(`${ORIGIN}/healthz`, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 400);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(3000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function ensureServer({ verbose = true } = {}) {
  if (await probe()) {
    if (verbose) console.log(`[server] :${PORT} already healthy`);
    return { started: false };
  }
  if (verbose) console.log(`[server] starting harness runserver on :${PORT} (embed=${EMBED_OVERRIDE})`);
  const child = spawn(PYTHON, ['manage.py', 'runserver', `127.0.0.1:${PORT}`, '--noreload'], {
    cwd: REPO_ROOT,
    env: { ...process.env, PRIMARY_EMBED_MODEL: EMBED_OVERRIDE, PYTHONIOENCODING: 'utf-8' },
    detached: true,
    stdio: 'ignore',
  });
  child.unref();
  const start = Date.now();
  while (Date.now() - start < 40000) {
    if (await probe()) {
      if (verbose) console.log(`[server] :${PORT} healthy (pid ${child.pid})`);
      return { started: true, pid: child.pid };
    }
    await sleep(1000);
  }
  throw new Error(`harness server on :${PORT} did not become healthy within 40s`);
}

module.exports = { ensureServer, probe };

if (require.main === module) {
  ensureServer().catch((e) => {
    console.error(e.message);
    process.exit(1);
  });
}