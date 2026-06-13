import { useEffect, useRef, useState } from 'react'

import type { DesktopFullFeatureDiagnostics } from '@/global'
import { desktopBridge } from '@/lib/desktop-bridge'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Wrench } from '@/lib/icons'

import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'

const DISMISSED_KEY = 'qiqiclaw:full-feature-diagnostics-dismissed'

interface FullFeatureDiagnosticsDialogProps {
  triggerKey?: number
}

export function FullFeatureDiagnosticsDialog({ triggerKey = 0 }: FullFeatureDiagnosticsDialogProps) {
  const [diagnostics, setDiagnostics] = useState<DesktopFullFeatureDiagnostics | null>(null)
  const [open, setOpen] = useState(false)
  const [checking, setChecking] = useState(false)
  const [repairing, setRepairing] = useState(false)
  const [repairOutput, setRepairOutput] = useState<string[]>([])
  const stoppedRef = useRef(false)
  const shownRef = useRef(false)

  const runCheck = async ({ background = false }: { background?: boolean } = {}) => {
    const bridge = desktopBridge()

    if (!bridge?.diagnostics?.check) {
      return null
    }

    if (!background) {
      setChecking(true)
    }

    try {
      const result = await bridge.diagnostics.check()
      setDiagnostics(result)
      return result
    } catch (error) {
      const result: DesktopFullFeatureDiagnostics = {
        enabled: true,
        ok: false,
        apiReachable: false,
        checkedAt: Date.now(),
        message: error instanceof Error ? error.message : String(error),
        checks: []
      }
      setDiagnostics(result)
      return result
    } finally {
      if (!background) {
        setChecking(false)
      }
    }
  }

  useEffect(() => {
    stoppedRef.current = false

    const loop = async ({ force = false }: { force?: boolean } = {}) => {
      const dismissedAt = Number(window.localStorage.getItem(DISMISSED_KEY) || '0')
      if (!force && dismissedAt > 0 && Date.now() - dismissedAt < 24 * 60 * 60 * 1000) {
        return
      }

      for (let attempt = 0; attempt < 40 && !stoppedRef.current && !shownRef.current; attempt += 1) {
        const result = await runCheck({ background: true })

        if (!result?.enabled) {
          return
        }

        if (result.apiReachable === false) {
          await new Promise(resolve => window.setTimeout(resolve, 15_000))
          continue
        }

        if (force) {
          window.localStorage.removeItem(DISMISSED_KEY)
        }
        shownRef.current = true
        setOpen(true)
        return
      }
    }

    void loop()

    return () => {
      stoppedRef.current = true
    }
  }, [])

  useEffect(() => {
    if (triggerKey <= 0) {
      return
    }

    stoppedRef.current = false
    shownRef.current = false
    void (async () => {
      const result = await runCheck()

      if (!result?.enabled || result.apiReachable === false) {
        return
      }

      window.localStorage.removeItem(DISMISSED_KEY)
      shownRef.current = true
      setOpen(true)
    })()
  }, [triggerKey])

  const missingChecks = diagnostics?.checks?.filter(check => !check.ok) ?? []
  const complete = Boolean(diagnostics?.ok)

  const close = () => {
    window.localStorage.setItem(DISMISSED_KEY, String(Date.now()))
    setOpen(false)
  }

  const repair = async () => {
    const bridge = desktopBridge()

    if (!bridge?.diagnostics?.repair) {
      return
    }

    setRepairing(true)
    setRepairOutput([])

    try {
      const result = await bridge.diagnostics.repair()
      setRepairOutput(result.output ?? [])
      await runCheck()
    } finally {
      setRepairing(false)
    }
  }

  if (!diagnostics?.enabled) {
    return null
  }

  return (
    <Dialog onOpenChange={value => (value ? setOpen(true) : close())} open={open}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle icon={complete ? CheckCircle2 : AlertTriangle}>
            {complete ? '全功能依赖完整' : '发现未安装的全功能依赖'}
          </DialogTitle>
          <DialogDescription>
            {complete
              ? 'QiQiClaw API 已连通，消息平台、语音输入和辅助功能依赖检测完整。'
              : 'QiQiClaw API 已连通，但部分消息平台、语音输入或辅助功能依赖尚未安装。'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 text-sm">
          <p className="text-muted-foreground">{diagnostics.message}</p>

          {missingChecks.length > 0 ? (
            <div className="flex max-h-40 flex-wrap gap-1.5 overflow-auto rounded-md border border-border bg-muted/30 p-2">
              {missingChecks.map(item => (
                <Badge key={item.id} variant="warn">
                  {item.label}
                </Badge>
              ))}
            </div>
          ) : null}

          {repairOutput.length > 0 ? (
            <pre className="max-h-36 w-full overflow-auto rounded-md bg-muted/50 p-2 text-left text-[0.68rem] leading-4">
              {repairOutput.join('\n')}
            </pre>
          ) : null}
        </div>

        <DialogFooter>
          <Button disabled={checking || repairing} onClick={() => void runCheck()} variant="secondary">
            {checking ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            重新检测
          </Button>
          {!complete ? (
            <Button disabled={checking || repairing} onClick={() => void repair()}>
              {repairing ? <Loader2 className="size-4 animate-spin" /> : <Wrench className="size-4" />}
              {repairing ? '配置中' : '配置缺失依赖'}
            </Button>
          ) : null}
          <Button disabled={repairing} onClick={close} variant={complete ? 'default' : 'outline'}>
            知道了
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
