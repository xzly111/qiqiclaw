/**
 * after-pack.cjs — electron-builder afterPack hook.
 *
 * Stamps the QiQiClaw icon + identity onto the packed Windows QiQiClaw.exe via
 * rcedit (delegated to set-exe-identity.cjs). This runs for EVERY packed build
 * — first install, `QiQiClaw Desktop`, the installer's --update rebuild, and a
 * dev's manual `npm run pack` — so the branded exe can never silently revert
 * to the stock "Electron" icon/name (the bug when the stamp lived only in
 * install.ps1, which the update path doesn't use).
 *
 * Windows-only: rcedit edits PE resources, irrelevant on macOS/Linux where the
 * app identity comes from the bundle Info.plist / desktop entry. Best-effort:
 * a stamp failure must never fail an otherwise-good build (worst case is the
 * stock icon, not a broken app), so we log and resolve rather than throw.
 *
 * electron-builder passes a context with:
 *   - electronPlatformName: 'win32' | 'darwin' | 'linux'
 *   - appOutDir:            the unpacked app directory for this target
 *   - packager.appInfo.productFilename: the exe basename (e.g. 'QiQiClaw')
 */

const path = require('node:path')
const fs = require('node:fs')

const { stampExeIdentity } = require('./set-exe-identity.cjs')

function removeBundledAppUpdateMetadata(appOutDir) {
  if (!appOutDir || typeof appOutDir !== 'string' || !fs.existsSync(appOutDir)) {
    return []
  }

  const removed = []
  const queue = [appOutDir]
  while (queue.length > 0) {
    const current = queue.shift()
    let entries
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name)
      if (entry.isDirectory()) {
        queue.push(fullPath)
        continue
      }
      if (entry.isFile() && entry.name === 'app-update.yml') {
        fs.rmSync(fullPath, { force: true })
        removed.push(fullPath)
      }
    }
  }
  return removed
}

exports.removeBundledAppUpdateMetadata = removeBundledAppUpdateMetadata

exports.default = async function afterPack(context) {
  try {
    const removed = removeBundledAppUpdateMetadata(context?.appOutDir)
    for (const file of removed) {
      console.log(`[after-pack] removed bundled app-update metadata: ${file}`)
    }
  } catch (err) {
    console.warn(`[after-pack] could not remove bundled app-update metadata (${err.message}); continuing`)
  }

  if (context.electronPlatformName !== 'win32') {
    return
  }

  const productName = context.packager?.appInfo?.productFilename || 'QiQiClaw'
  const exe = path.join(context.appOutDir, `${productName}.exe`)
  const desktopRoot = path.resolve(__dirname, '..')

  try {
    await stampExeIdentity(exe, desktopRoot)
  } catch (err) {
    // Never fail the build over a cosmetic stamp.
    console.warn(`[after-pack] exe identity stamp failed (${err.message}); QiQiClaw.exe keeps the stock Electron icon`)
  }
}
