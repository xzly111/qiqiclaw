const assert = require('node:assert/strict')
const test = require('node:test')

const {
  buildUpdateEnv,
  branchArgsForUpdate,
  defaultUpdateTmpDir,
  installSourceFromRuntime,
  mirrorEnvForInstallSource,
  normalizeInstallSource
} = require('./update-env.cjs')

test('normalizeInstallSource accepts only supported release sources', () => {
  assert.equal(normalizeInstallSource('gitee'), 'gitee')
  assert.equal(normalizeInstallSource(' GitHub '), 'github')
  assert.equal(normalizeInstallSource('unknown'), null)
})

test('installSourceFromRuntime prefers build stamp over environment', () => {
  assert.equal(
    installSourceFromRuntime({
      installStamp: { installSource: 'gitee' },
      env: { QIQICLAW_INSTALL_SOURCE: 'github' }
    }),
    'gitee'
  )
  assert.equal(installSourceFromRuntime({ installStamp: null, env: { HERMES_INSTALL_SOURCE: 'gitee' } }), 'gitee')
  assert.equal(installSourceFromRuntime({ installStamp: null, env: {} }), 'github')
})

test('mirrorEnvForInstallSource applies domestic mirrors for Gitee builds', () => {
  const env = mirrorEnvForInstallSource('gitee')
  assert.equal(env.QIQICLAW_INSTALL_SOURCE, 'gitee')
  assert.match(env.PIP_INDEX_URL, /tuna\.tsinghua\.edu\.cn/)
  assert.match(env.UV_INDEX_URL, /tuna\.tsinghua\.edu\.cn/)
  assert.match(env.npm_config_registry, /npmmirror\.com/)
  assert.match(env.QIQICLAW_NODE_DIST_BASE_URL, /npmmirror\.com/)
})

test('buildUpdateEnv forces source and mirrors over stale process values', () => {
  const env = buildUpdateEnv({
    installStamp: { installSource: 'gitee' },
    baseEnv: {
      QIQICLAW_INSTALL_SOURCE: 'github',
      PIP_INDEX_URL: 'https://pypi.org/simple',
      QIQICLAW_HOME: '/home/me/.qiqiclaw',
      PATH: '/usr/bin'
    },
    extraEnv: { HERMES_HOME: '/tmp/qiqiclaw' }
  })
  assert.equal(env.QIQICLAW_INSTALL_SOURCE, 'gitee')
  assert.equal(env.HERMES_INSTALL_SOURCE, 'gitee')
  assert.equal(env.HERMES_HOME, '/tmp/qiqiclaw')
  assert.match(env.PIP_INDEX_URL, /tuna\.tsinghua\.edu\.cn/)
  assert.equal(env.QIQICLAW_UPDATE_TMPDIR, '/home/me/.qiqiclaw/tmp')
  assert.equal(env.TMPDIR, '/home/me/.qiqiclaw/tmp')
  assert.equal(env.npm_config_cache, '/home/me/.qiqiclaw/tmp/npm-cache')
  assert.equal(env.PATH, '/usr/bin')
})

test('defaultUpdateTmpDir prefers the QiQiClaw home for rebuild scratch space', () => {
  assert.equal(defaultUpdateTmpDir({ QIQICLAW_HOME: '/home/me/.qiqiclaw/' }), '/home/me/.qiqiclaw/tmp')
  assert.equal(defaultUpdateTmpDir({ HERMES_HOME: '/home/me/.hermes' }), '/home/me/.hermes/tmp')
  assert.equal(defaultUpdateTmpDir({}), '')
})

test('branchArgsForUpdate omits default main for old CLI compatibility', () => {
  assert.deepEqual(branchArgsForUpdate('main'), [])
  assert.deepEqual(branchArgsForUpdate(''), [])
  assert.deepEqual(branchArgsForUpdate('release/2.0'), ['--branch', 'release/2.0'])
})
