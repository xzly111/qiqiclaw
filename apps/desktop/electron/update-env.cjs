'use strict'

const DEFAULT_UPDATE_BRANCH = 'main'
const DEFAULT_INSTALL_SOURCE = 'github'
const VALID_INSTALL_SOURCES = new Set(['github', 'gitee'])

function normalizeInstallSource(value) {
  const source = String(value || '').trim().toLowerCase()
  return VALID_INSTALL_SOURCES.has(source) ? source : null
}

function installSourceFromRuntime({ installStamp, env = process.env } = {}) {
  return (
    normalizeInstallSource(installStamp?.installSource) ||
    normalizeInstallSource(installStamp?.source) ||
    normalizeInstallSource(env.QIQICLAW_INSTALL_SOURCE) ||
    normalizeInstallSource(env.HERMES_INSTALL_SOURCE) ||
    DEFAULT_INSTALL_SOURCE
  )
}

function mirrorEnvForInstallSource(installSource) {
  const source = normalizeInstallSource(installSource) || DEFAULT_INSTALL_SOURCE
  const env = {
    QIQICLAW_INSTALL_SOURCE: source,
    HERMES_INSTALL_SOURCE: source
  }

  if (source === 'gitee') {
    env.PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    env.UV_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    env.UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    env.npm_config_registry = 'https://registry.npmmirror.com'
    env.PLAYWRIGHT_DOWNLOAD_HOST = 'https://npmmirror.com/mirrors/playwright'
    env.ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
    env.ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'
    env.QIQICLAW_NODE_DIST_BASE_URL = 'https://registry.npmmirror.com/-/binary/node'
  }

  return env
}

function buildUpdateEnv({ installStamp, baseEnv = process.env, extraEnv = {} } = {}) {
  const installSource = installSourceFromRuntime({ installStamp, env: baseEnv })
  const mirrorEnv = mirrorEnvForInstallSource(installSource)
  return {
    ...baseEnv,
    ...mirrorEnv,
    ...extraEnv,
    QIQICLAW_INSTALL_SOURCE: mirrorEnv.QIQICLAW_INSTALL_SOURCE,
    HERMES_INSTALL_SOURCE: mirrorEnv.HERMES_INSTALL_SOURCE
  }
}

function branchArgsForUpdate(branch, { defaultBranch = DEFAULT_UPDATE_BRANCH } = {}) {
  const normalized = String(branch || '').trim()
  if (!normalized || normalized === defaultBranch) {
    return []
  }
  return ['--branch', normalized]
}

module.exports = {
  DEFAULT_INSTALL_SOURCE,
  DEFAULT_UPDATE_BRANCH,
  buildUpdateEnv,
  branchArgsForUpdate,
  installSourceFromRuntime,
  mirrorEnvForInstallSource,
  normalizeInstallSource
}
