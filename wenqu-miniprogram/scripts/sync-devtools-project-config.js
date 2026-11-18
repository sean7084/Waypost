const fs = require('fs')
const path = require('path')

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

const rootDir = path.resolve(__dirname, '..')
const buildDir = path.join(rootDir, 'dist', 'build', 'mp-weixin')
const appJsonPath = path.join(buildDir, 'app.json')

if (!fs.existsSync(appJsonPath)) {
  throw new Error(`Missing compiled mini-program entry: ${appJsonPath}`)
}

const rootProjectConfigPath = path.join(rootDir, 'project.config.json')
const rootPrivateConfigPath = path.join(rootDir, 'project.private.config.json')
const buildProjectConfigPath = path.join(buildDir, 'project.config.json')
const buildPrivateConfigPath = path.join(buildDir, 'project.private.config.json')

const rootProjectConfig = readJson(rootProjectConfigPath)
const buildProjectConfig = {
  ...rootProjectConfig,
  miniprogramRoot: ''
}

writeJson(buildProjectConfigPath, buildProjectConfig)

if (fs.existsSync(rootPrivateConfigPath)) {
  const rootPrivateConfig = readJson(rootPrivateConfigPath)
  const buildPrivateConfig = {
    ...rootPrivateConfig,
    projectname: buildProjectConfig.projectname || rootPrivateConfig.projectname
  }
  writeJson(buildPrivateConfigPath, buildPrivateConfig)
}

console.log(`Synced DevTools project config into ${buildDir}`)
