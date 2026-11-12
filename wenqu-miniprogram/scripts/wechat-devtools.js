const fs = require('fs')
const os = require('os')
const path = require('path')
const { URLSearchParams } = require('url')

const rootDir = path.resolve(__dirname, '..')
const defaultPort = process.env.WECHAT_DEVTOOLS_PORT || process.env.WECHAT_IDE_PORT || '25066'
const buildDir = path.join(rootDir, 'dist', 'build', 'mp-weixin')

// DevTools picks a random service port per user profile and records it in a .ide file, so the
// 25066 default is only a first guess. Probing the recorded ports keeps the npm scripts working
// without exporting WECHAT_DEVTOOLS_PORT every time the IDE restarts.
function devtoolsPortCandidates() {
  const candidates = [defaultPort]
  const userDataRoot = path.join(
    process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'),
    '微信开发者工具',
    'User Data'
  )

  let profiles = []
  try {
    profiles = fs.readdirSync(userDataRoot)
  } catch (error) {
    return candidates
  }

  for (const profile of profiles) {
    try {
      const recorded = fs.readFileSync(path.join(userDataRoot, profile, 'Default', '.ide'), 'utf8').trim()
      if (recorded && !candidates.includes(recorded)) {
        candidates.push(recorded)
      }
    } catch (error) {
      // profile without a recorded port
    }
  }

  return candidates
}

let activePort = defaultPort

async function resolveActivePort() {
  for (const port of devtoolsPortCandidates()) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/v2/islogin`, { signal: AbortSignal.timeout(3000) })
      if (response.ok) {
        activePort = port
        return port
      }
    } catch (error) {
      // try the next candidate
    }
  }

  throw new Error(
    `No WeChat DevTools service port responded (tried ${devtoolsPortCandidates().join(', ')}). ` +
      'Open DevTools -> Settings -> Security Settings and turn on the service port, ' +
      'or set WECHAT_DEVTOOLS_PORT explicitly.'
  )
}

function usage() {
  console.error('Usage: node scripts/wechat-devtools.js <islogin|reset|clean|open|preview|upload> [--version <v>] [--desc <text>] [--robot <1-30>]')
}

function resolveBuildDir() {
  const appJsonPath = path.join(buildDir, 'app.json')
  if (!fs.existsSync(appJsonPath)) {
    throw new Error(`Missing compiled mini-program entry: ${appJsonPath}`)
  }
  return buildDir
}

function parseFlags(argv) {
  const flags = {}
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    if (!token.startsWith('--')) {
      continue
    }
    const key = token.slice(2)
    const value = argv[index + 1]
    if (value === undefined || value.startsWith('--')) {
      flags[key] = true
    } else {
      flags[key] = value
      index += 1
    }
  }
  return flags
}

function readManifestVersion() {
  for (const candidate of [path.join(rootDir, 'src', 'manifest.json'), path.join(rootDir, 'manifest.json')]) {
    if (!fs.existsSync(candidate)) {
      continue
    }
    const manifest = JSON.parse(fs.readFileSync(candidate, 'utf8'))
    if (manifest.versionName) {
      return String(manifest.versionName)
    }
  }
  return '1.0.0'
}

function buildUrl(endpoint, params = {}) {
  const searchParams = new URLSearchParams(params)
  const query = searchParams.toString()
  return `http://127.0.0.1:${activePort}${endpoint}${query ? `?${query}` : ''}`
}

async function requestJson(endpoint, params = {}) {
  const response = await fetch(buildUrl(endpoint, params))
  const bodyText = await response.text()
  if (!response.ok) {
    throw new Error(`${endpoint} failed with ${response.status}: ${bodyText}`)
  }
  try {
    return bodyText ? JSON.parse(bodyText) : {}
  } catch (error) {
    return { body: bodyText }
  }
}

async function preview() {
  const buildDir = resolveBuildDir()
  const qrOutput = path.join(rootDir, 'preview-build-root.png')
  const response = await fetch(buildUrl('/v2/preview', {
    project: buildDir,
    'qr-output': qrOutput
  }))
  const body = Buffer.from(await response.arrayBuffer())
  if (!response.ok) {
    throw new Error(`/v2/preview failed with ${response.status}: ${body.toString('utf8')}`)
  }
  fs.writeFileSync(qrOutput, body)
  return {
    project: buildDir,
    qrOutput
  }
}

async function upload(flags) {
  const project = resolveBuildDir()
  const version = flags.version || process.env.WECHAT_UPLOAD_VERSION || readManifestVersion()
  const desc = flags.desc || process.env.WECHAT_UPLOAD_DESC || `Uploaded via npm run devtools:upload (${new Date().toISOString()})`

  const params = { project, version, desc }
  if (flags.robot) {
    params.robot = String(flags.robot)
  }

  const result = await requestJson('/v2/upload', params)
  return { ...result, project, version, desc }
}

async function main() {
  const command = process.argv[2]
  if (!command) {
    usage()
    process.exit(1)
  }

  const flags = parseFlags(process.argv.slice(3))

  await resolveActivePort()
  console.error(`Using WeChat DevTools service port ${activePort}`)

  let result
  if (command === 'islogin') {
    result = await requestJson('/v2/islogin')
  } else if (command === 'reset') {
    result = await requestJson('/v2/resetfileutils', { project: resolveBuildDir() })
  } else if (command === 'clean') {
    result = await requestJson('/v2/cleancache', { clean: 'compile', project: resolveBuildDir() })
  } else if (command === 'open') {
    result = await requestJson('/v2/open', { project: resolveBuildDir() })
  } else if (command === 'preview') {
    result = await preview()
  } else if (command === 'upload') {
    result = await upload(flags)
  } else {
    usage()
    process.exit(1)
  }

  console.log(JSON.stringify(result, null, 2))
}

main().catch((error) => {
  console.error(error && error.message ? error.message : error)
  process.exit(1)
})
