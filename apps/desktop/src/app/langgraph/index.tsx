import { useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { AlertCircle, CheckCircle2, GitBranch, Loader2, Play, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { OverlayView } from '../overlays/overlay-view'

interface LangGraphStatus {
  available: boolean
  version: string
  error?: string
  source_path?: string
  modes: string[]
  capabilities: string[]
  workflow: {
    nodes: string[]
    edges: [string, string][]
    entrypoint?: string
    executor?: string
  }
}

interface LangGraphRunState {
  prompt: string
  model?: string | null
  provider?: string | null
  response?: string
  status?: string
  error?: string
  toolsets?: string[] | null
}

interface LangGraphRunResponse {
  ok: boolean
  dry_run: boolean
  state: LangGraphRunState
  workflow: {
    nodes: string[]
    edges: [string, string][]
  }
}

interface LangGraphViewProps {
  onClose: () => void
}

const DEFAULT_PROMPT = '规划一个三步 QiQiClaw 桌面端冒烟测试流程。'

function apiAvailable(): boolean {
  return typeof window !== 'undefined' && Boolean(window.hermesDesktop?.api)
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

export function LangGraphView({ onClose }: LangGraphViewProps) {
  const [status, setStatus] = useState<LangGraphStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [loadingStatus, setLoadingStatus] = useState(false)
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
  const [model, setModel] = useState('')
  const [provider, setProvider] = useState('')
  const [toolsets, setToolsets] = useState('files,terminal')
  const [dryRun, setDryRun] = useState(true)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<LangGraphRunResponse | null>(null)
  const [runError, setRunError] = useState('')

  const workflowText = useMemo(() => {
    const edges = status?.workflow.edges ?? result?.workflow.edges ?? []

    if (edges.length === 0) {
      return 'START -> qiqiclaw -> END'
    }

    return edges.map(([from, to]) => `${from} -> ${to}`).join('   ')
  }, [result?.workflow.edges, status?.workflow.edges])

  const loadStatus = async () => {
    setLoadingStatus(true)
    setStatusError('')
    try {
      if (!apiAvailable()) {
        throw new Error('QiQiClaw 桌面端 API 当前不可用。')
      }

      const payload = await window.hermesDesktop!.api<LangGraphStatus>({
        method: 'GET',
        path: '/api/langgraph/status',
        timeoutMs: 15_000
      })
      setStatus(payload)
    } catch (error) {
      setStatus(null)
      setStatusError(error instanceof Error ? error.message : '无法加载工作流状态。')
    } finally {
      setLoadingStatus(false)
    }
  }

  useEffect(() => {
    void loadStatus()
  }, [])

  const runWorkflow = async () => {
    const trimmedPrompt = prompt.trim()
    if (!trimmedPrompt) {
      setRunError('请输入要执行的任务。')
      setResult(null)
      return
    }

    setRunning(true)
    setRunError('')
    try {
      if (!apiAvailable()) {
        throw new Error('QiQiClaw 桌面端 API 当前不可用。')
      }

      const payload = await window.hermesDesktop!.api<LangGraphRunResponse>({
        body: {
          dry_run: dryRun,
          model: model.trim() || null,
          prompt: trimmedPrompt,
          provider: provider.trim() || null,
          toolsets: toolsets.trim() || null
        },
        method: 'POST',
        path: '/api/langgraph/run',
        timeoutMs: dryRun ? 30_000 : 300_000
      })
      setResult(payload)
    } catch (error) {
      setResult(null)
      setRunError(error instanceof Error ? error.message : '工作流执行失败。')
    } finally {
      setRunning(false)
    }
  }

  const ok = result?.ok
  const unavailable = Boolean(status && !status.available)

  return (
    <OverlayView
      closeLabel="关闭工作流"
      contentClassName="px-5 pt-[calc(var(--titlebar-height)+0.75rem)] pb-5 sm:px-6"
      onClose={onClose}
      rootClassName="mx-auto max-w-6xl"
    >
      <header className="mb-4 flex shrink-0 flex-col gap-3 border-b border-(--ui-stroke-tertiary) pb-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <GitBranch className="size-4 text-primary" />
            <h2 className="text-sm font-semibold text-foreground">工作流</h2>
            {status?.available ? (
              <Badge variant="default">
                <CheckCircle2 className="size-3" />
                可用
              </Badge>
            ) : unavailable ? (
              <Badge variant="destructive">
                <AlertCircle className="size-3" />
                不可用
              </Badge>
            ) : (
              <Badge variant="muted">{loadingStatus ? '加载中' : '未知'}</Badge>
            )}
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-[0.72rem] text-muted-foreground">
            <span>{workflowText}</span>
            {status?.version && <span>langgraph {status.version}</span>}
            {status?.source_path && <span className="truncate">源码 {status.source_path}</span>}
          </div>
        </div>
        <Button disabled={loadingStatus} onClick={loadStatus} size="sm" variant="secondary">
          {loadingStatus ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
          刷新
        </Button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
        <section className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-foreground" htmlFor="langgraph-prompt">
              任务
            </label>
            <Textarea
              className="min-h-32 resize-none text-xs leading-5"
              id="langgraph-prompt"
              onChange={event => setPrompt(event.target.value)}
              value={prompt}
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="模型" value={model} onChange={setModel} placeholder="可选" />
            <Field label="服务商" value={provider} onChange={setProvider} placeholder="可选" />
          </div>

          <Field label="工具集" value={toolsets} onChange={setToolsets} placeholder="files,terminal" />

          <label className="flex items-center gap-2 text-xs text-foreground">
            <Checkbox checked={dryRun} onCheckedChange={value => setDryRun(value === true)} />
            Dry-run 本地演练，不调用真实模型
          </label>

          {statusError && <InlineError message={statusError} />}
          {status?.error && !status.available && <InlineError message={status.error} />}
          {runError && <InlineError message={runError} />}
          {result && !result.ok && result.state.error && <InlineError message={result.state.error} />}

          <div className="flex items-center gap-2 pt-1">
            <Button disabled={running || unavailable} onClick={runWorkflow}>
              {running ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}
              运行工作流
            </Button>
            <Badge variant={dryRun ? 'muted' : 'warn'}>{dryRun ? '本地演练' : '真实 Agent'}</Badge>
          </div>

          <div className="mt-2 grid gap-2 text-[0.72rem] text-muted-foreground">
            <MetaRow label="入口模块" value={status?.workflow.entrypoint ?? 'qiqiclaw_cli.langgraph_runner'} />
            <MetaRow label="执行节点" value={status?.workflow.executor ?? 'qiqiclaw_cli.oneshot._run_agent'} />
            <MetaRow label="模式" value={(status?.modes ?? ['dry-run', 'agent']).map(formatMode).join('、')} />
            <MetaRow label="能力" value={(status?.capabilities ?? []).map(formatCapability).join('、')} />
          </div>
        </section>

        <section className="flex min-h-0 flex-col gap-3 overflow-hidden border-l-0 border-(--ui-stroke-tertiary) lg:border-l lg:pl-4">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold text-foreground">执行结果</h3>
            {result && <Badge variant={ok ? 'default' : 'destructive'}>{ok ? '成功' : '错误'}</Badge>}
          </div>

          <div className="min-h-24 rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-3 text-xs leading-5 text-foreground">
            {result?.state.response ? (
              <pre className="whitespace-pre-wrap break-words font-sans">{result.state.response}</pre>
            ) : (
              <span className="text-muted-foreground">尚未运行工作流。</span>
            )}
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-2">
            <h3 className="text-xs font-semibold text-foreground">图状态</h3>
            <pre
              className={cn(
                'min-h-0 flex-1 overflow-auto rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-3 font-mono text-[0.72rem] leading-5 text-muted-foreground',
                !result && 'text-muted-foreground/70'
              )}
            >
              {formatJson(result?.state ?? { status: 'idle', workflow: workflowText })}
            </pre>
          </div>
        </section>
      </div>
    </OverlayView>
  )
}

function Field({
  label,
  onChange,
  placeholder,
  value
}: {
  label: string
  onChange: (value: string) => void
  placeholder?: string
  value: string
}) {
  const id = `langgraph-${label.replace(/\s+/g, '-').toLowerCase()}`

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-foreground" htmlFor={id}>
        {label}
      </label>
      <Input
        id={id}
        onChange={event => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
    </div>
  )
}

function formatMode(mode: string): string {
  if (mode === 'dry-run') {
    return '本地演练'
  }
  if (mode === 'agent') {
    return '真实 Agent'
  }
  return mode
}

function formatCapability(capability: string): string {
  const labels: Record<string, string> = {
    edges: '边',
    mcp: 'MCP',
    nodes: '节点',
    'qiqiclaw-memory': 'QiQiClaw 记忆',
    'qiqiclaw-tools': 'QiQiClaw 工具',
    skills: '技能',
    state: '状态',
    stategraph: '状态图'
  }

  return labels[capability] ?? capability
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-[6px] border border-destructive/25 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0 break-words">{message}</span>
    </div>
  )
}

function MetaRow({ label, value }: { label: string; value: string }) {
  if (!value) {
    return null
  }

  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2">
      <span className="text-muted-foreground/75">{label}</span>
      <span className="min-w-0 break-words text-muted-foreground">{value}</span>
    </div>
  )
}
