import { useEffect, useState } from 'react'

import type { DesktopFullFeatureDiagnostics } from '@/global'
import { desktopBridge } from '@/lib/desktop-bridge'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Wrench } from '@/lib/icons'

import { Alert, AlertDescription, AlertTitle } from './ui/alert'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

interface FullFeatureDiagnosticsPanelProps {
  className?: string
  showTitle?: boolean
}

export function FullFeatureDiagnosticsPanel({ className, showTitle = true }: FullFeatureDiagnosticsPanelProps) {
  const [diagnostics, setDiagnostics] = useState<DesktopFullFeatureDiagnostics | null>(null)
  const [loading, setLoading] = useState(false)
  const [repairing, setRepairing] = useState(false)
  const [repairOutput, setRepairOutput] = useState<string[]>([])

  const runDiagnostics = async () => {
    const bridge = desktopBridge()

    if (!bridge?.diagnostics?.check) {
      return
    }

    setLoading(true)

    try {
      const result = await bridge.diagnostics.check()
      setDiagnostics(result)
    } catch (error) {
      setDiagnostics({
        enabled: true,
        ok: false,
        apiReachable: false,
        checkedAt: Date.now(),
        message: error instanceof Error ? error.message : String(error),
        checks: []
      })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void runDiagnostics()
  }, [])

  const missingChecks = diagnostics?.checks?.filter(check => !check.ok) ?? []

  const repair = async () => {
    const bridge = desktopBridge()

    if (!bridge?.diagnostics?.repair) {
      return
    }

    if (!window.confirm('检测到部分辅助功能依赖缺失，是否现在修复？修复会使用当前安装包对应的源继续安装缺失依赖。')) {
      return
    }

    setRepairing(true)
    setRepairOutput([])

    try {
      const result = await bridge.diagnostics.repair()
      setRepairOutput(result.output ?? [])
      await runDiagnostics()
    } finally {
      setRepairing(false)
    }
  }

  if (!diagnostics?.enabled) {
    return null
  }

  return (
    <div className={className}>
      {showTitle ? (
        <div className="mb-2 flex items-center gap-2 text-sm font-medium">
          <CheckCircle2 className="size-4 text-primary" />
          <span>全功能检测</span>
        </div>
      ) : null}
      <Alert variant={diagnostics.ok ? 'success' : diagnostics.apiReachable === false ? 'destructive' : 'warning'}>
        {diagnostics.ok ? (
          <CheckCircle2 />
        ) : diagnostics.apiReachable === false ? (
          <AlertTriangle />
        ) : (
          <Wrench />
        )}
        <AlertTitle>
          {loading
            ? '正在检测'
            : diagnostics.ok
              ? '功能完整'
              : diagnostics.apiReachable === false
                ? 'API 未连通'
                : '发现可修复项目'}
        </AlertTitle>
        <AlertDescription>
          <p>{loading ? '正在确认 API 连通和可选功能依赖。' : diagnostics.message}</p>
          {missingChecks.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {missingChecks.slice(0, 10).map(item => (
                <Badge key={item.id} variant="warn">
                  {item.label}
                </Badge>
              ))}
              {missingChecks.length > 10 ? <Badge variant="muted">+{missingChecks.length - 10}</Badge> : null}
            </div>
          ) : null}
          {repairOutput.length > 0 ? (
            <pre className="mt-2 max-h-32 w-full overflow-auto rounded-md bg-muted/50 p-2 text-left text-[0.68rem] leading-4">
              {repairOutput.join('\n')}
            </pre>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button disabled={loading || repairing} onClick={() => void runDiagnostics()} size="sm" variant="secondary">
              {loading ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
              重新检测
            </Button>
            {!diagnostics.ok && diagnostics.apiReachable !== false ? (
              <Button disabled={loading || repairing} onClick={() => void repair()} size="sm">
                {repairing ? <Loader2 className="size-3 animate-spin" /> : <Wrench className="size-3" />}
                {repairing ? '修复中' : '修复缺失依赖'}
              </Button>
            ) : null}
          </div>
        </AlertDescription>
      </Alert>
    </div>
  )
}
