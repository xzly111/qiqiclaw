const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { removeBundledAppUpdateMetadata } = require('../scripts/after-pack.cjs')

test('removeBundledAppUpdateMetadata removes bundled app-update.yml files recursively', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'qiqiclaw-after-pack-'))
  try {
    const resources = path.join(tempRoot, 'QiQiClaw.app', 'Contents', 'Resources')
    fs.mkdirSync(resources, { recursive: true })
    const updateFile = path.join(resources, 'app-update.yml')
    const nestedFile = path.join(tempRoot, 'linux-unpacked', 'resources', 'app-update.yml')
    fs.mkdirSync(path.dirname(nestedFile), { recursive: true })
    fs.writeFileSync(updateFile, 'provider: github\n', 'utf8')
    fs.writeFileSync(nestedFile, 'provider: github\n', 'utf8')

    const removed = removeBundledAppUpdateMetadata(tempRoot)

    assert.equal(fs.existsSync(updateFile), false)
    assert.equal(fs.existsSync(nestedFile), false)
    assert.equal(removed.length, 2)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('removeBundledAppUpdateMetadata is a no-op for missing paths', () => {
  assert.deepEqual(removeBundledAppUpdateMetadata(''), [])
  assert.deepEqual(removeBundledAppUpdateMetadata(undefined), [])
  assert.deepEqual(removeBundledAppUpdateMetadata('/tmp/does-not-exist-qiqiclaw'), [])
})
