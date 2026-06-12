import type * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { StatusDot, type StatusTone } from '@/components/status-dot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  getMessagingPlatforms,
  restartGateway,
  type MessagingEnvVarInfo,
  type MessagingPlatformInfo,
  type MessagingPlatformsResponse,
  updateMessagingPlatform
} from '@/hermes'
import { type Translations, useI18n } from '@/i18n'
import { AlertTriangle, RefreshCw, Save, Trash2 } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { PageSearchShell } from '../page-search-shell'
import { CREDENTIAL_CONTROL_CLASS } from '../settings/credential-key-ui'
import { ListRow } from '../settings/primitives'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

import { PlatformAvatar } from './platform-icon'

interface MessagingViewProps extends React.ComponentProps<'section'> {
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

type EditMap = Record<string, Record<string, string>>

const PILL_TONE: Record<StatusTone, string> = {
  good: 'bg-primary/10 text-primary',
  muted: 'bg-muted text-muted-foreground',
  warn: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
  bad: 'bg-destructive/10 text-destructive'
}

const stateLabel = (state: null | string | undefined, m: Translations['messaging']) =>
  state ? m.states[state] || state.replace(/_/g, ' ') : m.unknown

function stateTone({ configured, gateway_running, state }: MessagingPlatformInfo): StatusTone {
  if (state === 'fatal' || state === 'startup_failed') {
    return 'bad'
  }
  if (configured && gateway_running && (state === 'connected' || state === 'configured')) {
    return 'good'
  }
  if (configured) {
    return 'warn'
  }
  return 'muted'
}

const trimEdits = (edits: Record<string, string>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(edits)
      .map(([k, v]) => [k, v.trim()])
      .filter(([, v]) => v)
  )

function fieldCopy(field: MessagingEnvVarInfo, m: Translations['messaging']) {
  const localized = m.fieldCopy[field.key] || {}
  return {
    help: localized.help || field.description,
    label: localized.label || field.prompt || field.key,
    placeholder: localized.placeholder || field.prompt
  }
}

export function MessagingView({ setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: MessagingViewProps) {
  const { t } = useI18n()
  const m = t.messaging
  const [response, setResponse] = useState<MessagingPlatformsResponse | null>(null)
  const platforms = response?.platforms ?? null
  const [edits, setEdits] = useState<EditMap>({})
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const platformIds = useMemo(() => platforms?.map(p => p.id) ?? [], [platforms])
  const [selectedId, setSelectedId] = useRouteEnumParam('platform', platformIds, platformIds[0] ?? '')

  const refreshPlatforms = useCallback(
    async (silent = false) => {
      if (!silent) {
        setRefreshing(true)
      }

      try {
        setResponse(await getMessagingPlatforms())
      } catch (err) {
        if (!silent) {
          notifyError(err, m.loadFailed)
        }
      } finally {
        if (!silent) {
          setRefreshing(false)
        }
      }
    },
    [m]
  )

  useRefreshHotkey(() => void refreshPlatforms())

  useEffect(() => {
    void refreshPlatforms()
  }, [refreshPlatforms])

  useEffect(() => {
    let cancelled = false

    function tick() {
      if (cancelled || document.hidden) {
        return
      }
      void refreshPlatforms(true)
    }

    const id = window.setInterval(tick, 6000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [refreshPlatforms])

  const selected = useMemo(() => {
    if (!platforms) {
      return null
    }
    return platforms.find(platform => platform.id === selectedId) || platforms[0] || null
  }, [platforms, selectedId])

  const visiblePlatforms = useMemo(() => {
    if (!platforms) {
      return []
    }
    const q = query.trim().toLowerCase()
    if (!q) {
      return platforms
    }
    return platforms.filter(platform =>
      [platform.id, platform.name, platform.setup_status, platform.state]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(q))
    )
  }, [platforms, query])

  async function handleSave(platform: MessagingPlatformInfo) {
    const env = trimEdits(edits[platform.id] || {})
    if (Object.keys(env).length === 0) {
      return
    }

    setSaving(`env:${platform.id}`)
    try {
      await updateMessagingPlatform(platform.id, { env })
      setEdits(current => ({ ...current, [platform.id]: {} }))
      await refreshPlatforms()
      notify({
        kind: 'success',
        title: m.setupSaved(platform.name),
        message: m.restartToReconnect
      })
    } catch (err) {
      notifyError(err, m.failedSave(platform.name))
    } finally {
      setSaving(null)
    }
  }

  async function handleClear(platform: MessagingPlatformInfo, key: string) {
    setSaving(`clear:${key}`)
    try {
      await updateMessagingPlatform(platform.id, { clear_env: [key] })
      setEdits(current => ({
        ...current,
        [platform.id]: {
          ...(current[platform.id] || {}),
          [key]: ''
        }
      }))
      await refreshPlatforms()
      notify({ kind: 'success', title: m.keyCleared(key), message: m.setupUpdated(platform.name) })
    } catch (err) {
      notifyError(err, m.failedClear(key))
    } finally {
      setSaving(null)
    }
  }

  async function handleRestartGateway() {
    setSaving('gateway-restart')
    try {
      const result = await restartGateway()
      notify({ kind: 'success', title: m.gatewayRestartStarted, message: `${result.name} PID ${result.pid}` })
      await refreshPlatforms()
    } catch (err) {
      notifyError(err, m.gatewayRestartFailed)
    } finally {
      setSaving(null)
    }
  }

  return (
    <PageSearchShell
      {...props}
      onSearchChange={setQuery}
      searchHidden={(platforms?.length ?? 0) === 0}
      searchPlaceholder={m.search}
      searchValue={query}
    >
      {!platforms ? (
        <PageLoader label={m.loading} />
      ) : (
        <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-y-auto border-r border-border/60 p-2">
            <GatewaySummary response={response} onRestart={() => void handleRestartGateway()} restarting={saving === 'gateway-restart'} />
            <ul className="mt-2 space-y-1">
              {visiblePlatforms.map(platform => (
                <li key={platform.id}>
                  <PlatformRow
                    active={selected?.id === platform.id}
                    onSelect={() => setSelectedId(platform.id)}
                    platform={platform}
                  />
                </li>
              ))}
            </ul>
          </aside>

          <main className="min-h-0 overflow-hidden">
            {selected && (
              <PlatformDetail
                edits={edits[selected.id] || {}}
                onClear={key => void handleClear(selected, key)}
                onEdit={(key, value) =>
                  setEdits(current => ({
                    ...current,
                    [selected.id]: {
                      ...(current[selected.id] || {}),
                      [key]: value
                    }
                  }))
                }
                onSave={() => void handleSave(selected)}
                platform={selected}
                refreshing={refreshing}
                saving={saving}
              />
            )}
          </main>
        </div>
      )}
    </PageSearchShell>
  )
}

function GatewaySummary({
  onRestart,
  response,
  restarting
}: {
  onRestart: () => void
  response: MessagingPlatformsResponse | null
  restarting: boolean
}) {
  const running = Boolean(response?.service_running || (response?.gateway_pids?.length ?? 0) > 0)
  const configuredCount = response?.platforms.filter(platform => platform.configured).length ?? 0

  return (
    <div className="rounded-md border border-border/70 px-2 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 font-medium">
          <StatusDot tone={running ? 'good' : 'warn'} />
          <span className="truncate">Gateway</span>
        </span>
        <Button disabled={restarting} onClick={onRestart} size="xs" variant="ghost">
          <RefreshCw className="size-3.5" />
        </Button>
      </div>
      <div className="mt-1 text-muted-foreground">
        {running ? `PID ${(response?.gateway_pids || []).join(', ') || '-'}` : 'Stopped'}
      </div>
      <div className="mt-1 text-muted-foreground">{configuredCount} configured</div>
    </div>
  )
}

function PlatformRow({
  active,
  onSelect,
  platform
}: {
  active: boolean
  onSelect: () => void
  platform: MessagingPlatformInfo
}) {
  return (
    <button
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
        active
          ? 'bg-(--ui-row-active-background) text-foreground'
          : 'text-(--ui-text-secondary) hover:bg-(--ui-row-hover-background) hover:text-foreground'
      )}
      onClick={onSelect}
      type="button"
    >
      <PlatformAvatar platformId={platform.id} platformName={platform.name} />
      <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
        <span className="truncate text-[length:var(--conversation-text-font-size)] font-normal">{platform.name}</span>
        <StatusDot tone={stateTone(platform)} />
      </span>
    </button>
  )
}

function PlatformDetail({
  edits,
  onClear,
  onEdit,
  onSave,
  platform,
  saving
}: {
  edits: Record<string, string>
  onClear: (key: string) => void
  onEdit: (key: string, value: string) => void
  onSave: () => void
  platform: MessagingPlatformInfo
  refreshing: boolean
  saving: string | null
}) {
  const { t } = useI18n()
  const m = t.messaging
  const hasEdits = Object.keys(trimEdits(edits)).length > 0
  const requiredFields = platform.env_vars.filter(field => field.required)
  const optionalFields = platform.env_vars.filter(field => !field.required)
  const isSavingEnv = saving === `env:${platform.id}`

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-5 px-5 py-4">
          <header className="flex items-start gap-3">
            <PlatformAvatar platformId={platform.id} platformName={platform.name} />
            <div className="min-w-0 flex-1">
              <h3 className="text-[0.9375rem] font-semibold tracking-tight">{platform.name}</h3>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatePill tone={stateTone(platform)}>{stateLabel(platform.state, m)}</StatePill>
                <SetupPill active={platform.configured}>{platform.setup_status || m.needsSetup}</SetupPill>
                {!platform.gateway_running && <SetupPill active={false}>{m.gatewayStopped}</SetupPill>}
              </div>
              <PlatformHint platform={platform} />
            </div>
          </header>

          {platform.error_message && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-destructive">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{platform.error_message}</span>
            </div>
          )}

          {platform.setup_instructions && platform.setup_instructions.length > 0 && (
            <section>
              <SectionTitle>{m.getCredentials}</SectionTitle>
              <ol className="mt-2 space-y-1.5 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                {platform.setup_instructions.map((line, index) => (
                  <li className="font-mono" key={`${index}:${line}`}>
                    {line}
                  </li>
                ))}
              </ol>
            </section>
          )}

          <section>
            <SectionTitle>{m.required}</SectionTitle>
            <div className="mt-3 grid gap-1">
              {requiredFields.length > 0 ? (
                requiredFields.map(field => (
                  <MessagingField
                    edits={edits}
                    field={field}
                    key={field.key}
                    onClear={onClear}
                    onEdit={onEdit}
                    saving={saving}
                  />
                ))
              ) : (
                <p className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                  {m.noTokenNeeded}
                </p>
              )}
            </div>
          </section>

          {optionalFields.length > 0 && (
            <section>
              <SectionTitle>{m.recommended}</SectionTitle>
              <div className="mt-3 grid gap-1">
                {optionalFields.map(field => (
                  <MessagingField
                    edits={edits}
                    field={field}
                    key={field.key}
                    onClear={onClear}
                    onEdit={onEdit}
                    saving={saving}
                  />
                ))}
              </div>
            </section>
          )}

          {platform.install_hint && (
            <section>
              <SectionTitle>Install</SectionTitle>
              <p className="mt-2 font-mono text-xs text-muted-foreground">{platform.install_hint}</p>
            </section>
          )}
        </div>
      </div>

      <footer className="bg-(--ui-chat-surface-background) px-5 py-2.5">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-end gap-2">
          {hasEdits && <span className="text-xs text-muted-foreground">{m.unsavedChanges}</span>}
          <Button disabled={!hasEdits || isSavingEnv} onClick={onSave} size="sm">
            <Save />
            {isSavingEnv ? m.saving : m.saveChanges}
          </Button>
        </div>
      </footer>
    </div>
  )
}

function MessagingField({
  edits,
  field,
  onClear,
  onEdit,
  saving
}: {
  edits: Record<string, string>
  field: MessagingEnvVarInfo
  onClear: (key: string) => void
  onEdit: (key: string, value: string) => void
  saving: string | null
}) {
  const { t } = useI18n()
  const m = t.messaging
  const copy = fieldCopy(field, m)
  const fieldId = `messaging-field-${field.key}`

  return (
    <ListRow
      action={
        <div className="flex items-center gap-2">
          <Input
            className={CREDENTIAL_CONTROL_CLASS}
            id={fieldId}
            onChange={event => onEdit(field.key, event.target.value)}
            placeholder={field.is_set ? field.redacted_value || m.replaceValue : copy.placeholder}
            type={field.is_password ? 'password' : 'text'}
            value={edits[field.key] || ''}
          />
          {field.is_set && (
            <Button
              className="size-8 shrink-0"
              disabled={saving === `clear:${field.key}`}
              onClick={() => onClear(field.key)}
              title={m.clearField(field.key)}
              variant="ghost"
            >
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      }
      description={copy.help}
      title={
        <span className="flex flex-wrap items-center gap-2">
          <label htmlFor={fieldId}>{copy.label}</label>
          {field.is_set && <span className="text-[0.66rem] font-medium text-primary">{m.saved}</span>}
        </span>
      }
    />
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{children}</h4>
}

function PlatformHint({ platform }: { platform: MessagingPlatformInfo }) {
  const { t } = useI18n()

  if (platform.state === 'connected') {
    return null
  }

  const hint =
    platform.state === 'pending_restart'
      ? t.messaging.hintPendingRestart
      : platform.configured && !platform.gateway_running
        ? t.messaging.hintGatewayStopped
        : null

  return hint ? <p className="mt-2 text-xs leading-5 text-muted-foreground">{hint}</p> : null
}

function StatePill({ children, tone }: { children: string; tone: StatusTone }) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.66rem] font-medium',
        PILL_TONE[tone]
      )}
    >
      <StatusDot tone={tone} />
      {children}
    </span>
  )
}

function SetupPill({ active, children }: { active: boolean; children: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[0.66rem] font-medium',
        PILL_TONE[active ? 'good' : 'muted']
      )}
    >
      {children}
    </span>
  )
}
