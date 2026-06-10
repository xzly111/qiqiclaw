import { Leva, useControls } from 'leva'
import { useEffect, useState } from 'react'

export function Backdrop() {
  const [controlsOpen, setControlsOpen] = useState(false)

  useEffect(() => {
    if (!import.meta.env.DEV) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null

      const editing =
        target?.isContentEditable ||
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement

      if (editing || event.repeat || event.altKey || event.ctrlKey || event.metaKey) {
        return
      }

      if (event.shiftKey && event.code === 'KeyY') {
        setControlsOpen(open => !open)
      }
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const shape = useControls(
    'UI / Shape',
    { radiusScalar: { value: 0.2, min: 0, max: 2, step: 0.1, label: 'radius scalar' } },
    { collapsed: true }
  )

  useEffect(() => {
    document.documentElement.style.setProperty('--radius-scalar', String(shape.radiusScalar))
  }, [shape.radiusScalar])

  const wordmark = useControls(
    'Backdrop / Wordmark',
    {
      enabled: { value: true, label: 'on' },
      opacity: { value: 0.035, min: 0, max: 0.2, step: 0.005 },
      scale: { value: 1, min: 0.5, max: 2, step: 0.05 }
    },
    { collapsed: true }
  )

  return (
    <>
      <Leva collapsed hidden={!import.meta.env.DEV || !controlsOpen} titleBar={{ title: 'backdrop', drag: true }} />

      {wordmark.enabled && (
        <div aria-hidden className="pointer-events-none absolute inset-0 z-2 overflow-hidden">
          <div
            className="qiqiclaw-backdrop-wordmark"
            style={{
              opacity: wordmark.opacity,
              transform: `scale(${wordmark.scale})`
            }}
          >
            QIQI-Claw
          </div>
        </div>
      )}
    </>
  )
}
