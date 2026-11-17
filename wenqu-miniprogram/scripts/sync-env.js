const fs = require('fs')
const path = require('path')

const rootDir = path.resolve(__dirname, '..')
// An optional positional name selects an extra overlay, e.g. `node sync-env.js test`
// reads .env.test last. Production scripts pass no name, so they cannot pick up a test URL.
const envName = process.argv[2]
const envFiles = envName ? ['.env', '.env.local', `.env.${envName}`] : ['.env', '.env.local']
const outputPath = path.join(rootDir, 'src', 'utils', 'config.local.js')

function parseEnvFile(filePath, target) {
  const content = fs.readFileSync(filePath, 'utf8')
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) {
      continue
    }

    const separatorIndex = line.indexOf('=')
    if (separatorIndex === -1) {
      continue
    }

    const key = line.slice(0, separatorIndex).trim()
    let value = line.slice(separatorIndex + 1).trim()

    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1)
    }

    target[key] = value.replace(/\\n/g, '\n')
  }
}

const values = {}
let hasEnvFile = false

for (const envFile of envFiles) {
  const envPath = path.join(rootDir, envFile)
  if (!fs.existsSync(envPath)) {
    continue
  }
  hasEnvFile = true
  parseEnvFile(envPath, values)
}

if (!hasEnvFile) {
  console.error('No .env or .env.local file found in frontend.')
  process.exit(1)
}

const requiredKeys = ['API_BASE_URL']
const missingKeys = requiredKeys.filter((key) => !values[key])

if (missingKeys.length) {
  console.error(`Missing required env keys: ${missingKeys.join(', ')}`)
  process.exit(1)
}

const normalizedApiBaseUrl = String(values.API_BASE_URL).replace(/\/+$/, '')

const fileText = [
  `export const API_BASE_URL = ${JSON.stringify(normalizedApiBaseUrl)}`,
  ''
].join('\n')

fs.writeFileSync(outputPath, fileText, 'utf8')
console.log(`Wrote ${path.relative(rootDir, outputPath)}`)
console.log(`API_BASE_URL = ${normalizedApiBaseUrl}${envName ? `  (overlay: .env.${envName})` : '  (production)'}`)