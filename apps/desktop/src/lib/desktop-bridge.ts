export type DesktopBridge = Window['qiqiDesktop']

export function desktopBridge(): DesktopBridge | undefined {
  if (typeof window === 'undefined') {
    return undefined
  }

  return window.qiqiDesktop ?? window.hermesDesktop
}
