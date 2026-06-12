import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  addCredentialPoolEntry,
  discoverProviderModels,
  getCredentialPool,
  getGlobalModelOptions,
  getProviderCatalog,
  removeCredentialPoolEntry,
  validateModelRoute,
} from '@/hermes'
import { useI18n } from '@/i18n'
import { AlertCircle, CheckCircle2, Cpu, KeyRound, Loader2, Plus, RefreshCw, Trash2 } from '@/lib/icons'
import { cn } from '@/lib/utils'
import type { CredentialPoolProvider, ModelOptionProvider, ProviderCatalogEntry } from '@/types/hermes'

import { SettingsCategoryHeading } from './env-credentials'
import { ModelLibrarySettings } from './model-library-settings'
import { SettingsContent } from './primitives'
import {
  CREDENTIAL_POOL_PROVIDER_OPTIONS,
  CUSTOM_PROVIDER_LABEL,
  CUSTOM_PROVIDER_SLUG,
  ENDPOINT_PRESETS,
  detectProviderFromBaseUrl,
  providerLabel
} from './provider-options'

// Sub-views mirror the standalone AppImage information architecture: model
// routing first, provider credentials second.
export const PROVIDER_VIEWS = ['models', 'providers'] as const

export type ProviderView = (typeof PROVIDER_VIEWS)[number]

type RouteStatus = 'idle' | 'loading' | 'ok' | 'error'

function ModelRoutePanel({
  catalog,
  modelProviders,
  onDiscoveredModels,
  onOptionsChanged
}: {
  catalog: ProviderCatalogEntry[]
  modelProviders: ModelOptionProvider[]
  onDiscoveredModels: (providers: ModelOptionProvider[]) => void
  onOptionsChanged: (providers: ModelOptionProvider[]) => void
}) {
  const [provider, setProvider] = useState(CUSTOM_PROVIDER_SLUG)
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('https://oneapi.hk/v1')
  const [providerTouched, setProviderTouched] = useState(false)
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [status, setStatus] = useState<RouteStatus>('idle')
  const [message, setMessage] = useState('')
  const [discovering, setDiscovering] = useState(false)

  const providerOptions = useMemo(() => {
    const fromCatalog = catalog.map(row => ({ label: row.name || row.slug, value: row.slug }))
    const fromModels = modelProviders
      .map(row => ({ label: row.name || row.slug, value: row.slug }))
      .filter(row => row.value)
    const seen = new Set<string>()

    return [CREDENTIAL_POOL_PROVIDER_OPTIONS[0], ...fromCatalog, ...fromModels, ...CREDENTIAL_POOL_PROVIDER_OPTIONS]
      .filter(row => {
        if (seen.has(row.value)) {
          return false
        }
        seen.add(row.value)
        return true
      })
      .sort((a, b) => (a.value === CUSTOM_PROVIDER_SLUG ? -1 : b.value === CUSTOM_PROVIDER_SLUG ? 1 : a.label.localeCompare(b.label)))
  }, [catalog, modelProviders])

  const providerModels = modelProviders.find(row => row.slug === provider)?.models ?? []
  const modelOptions = [...new Set([...providerModels, ...discoveredModels, model].filter(Boolean))]
  const selectedLabel = provider === CUSTOM_PROVIDER_SLUG ? CUSTOM_PROVIDER_LABEL : providerLabel(provider, modelProviders)
  const selectedCatalogProvider = catalog.find(row => row.slug === provider)

  useEffect(() => {
    if (provider || providerOptions.length === 0) return
    setProvider(providerOptions[0].value)
  }, [provider, providerOptions])

  useEffect(() => {
    if (providerTouched || !baseUrl.trim()) return
    const detected = detectProviderFromBaseUrl(baseUrl)
    if (detected && detected !== provider) {
      setProvider(detected)
    }
  }, [baseUrl, provider, providerTouched])

  useEffect(() => {
    if (provider === CUSTOM_PROVIDER_SLUG) return
    const defaultBaseUrl = selectedCatalogProvider?.base_url ?? ''
    if (defaultBaseUrl && baseUrl !== defaultBaseUrl) {
      setBaseUrl(defaultBaseUrl)
    }
  }, [baseUrl, provider, selectedCatalogProvider?.base_url])

  async function discover() {
    const trimmedProvider = provider.trim()
    const url = baseUrl.trim()
    if (trimmedProvider === CUSTOM_PROVIDER_SLUG && !url) {
      setStatus('error')
      setMessage('请先填写 Base URL。')
      return
    }
    setDiscovering(true)
    setMessage('')
    try {
      const result = await discoverProviderModels({
        provider: trimmedProvider,
        ...(url ? { base_url: url } : {})
      })
      if (!result.ok) {
        setStatus('error')
        setMessage(result.message || '没有发现可用模型。')
        return
      }
      const models = result.models ?? []
      setDiscoveredModels(models)
      const options = await getGlobalModelOptions()
      onDiscoveredModels(options.providers ?? [])
      setStatus('ok')
      setMessage(result.checked?.length
        ? `已检测 ${result.checked.length} 个凭证，发现并加入模型库 ${result.saved_count ?? models.length} 个可用模型。`
        : `已发现并加入模型库 ${result.saved_count ?? models.length} 个可用模型。`)
      if (!model && models[0]) {
        setModel(models[0])
      }
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setDiscovering(false)
    }
  }

  async function validateRoute() {
    const trimmedProvider = provider.trim()
    const trimmedModel = model.trim()
    const trimmedBaseUrl = baseUrl.trim()
    if (!trimmedProvider || !trimmedModel) {
      setStatus('error')
      setMessage('请填写提供商和模型。')
      return
    }
    if (trimmedProvider === CUSTOM_PROVIDER_SLUG && !trimmedBaseUrl) {
      setStatus('error')
      setMessage('OpenAI 兼容 / 中转站 / 本地必须填写 Base URL。')
      return
    }

    setStatus('loading')
    setMessage('')
    try {
      const result = await validateModelRoute({
        provider: trimmedProvider,
        model: trimmedModel,
        base_url: trimmedBaseUrl,
        name: trimmedModel
      })
      if (result.options?.providers) {
        onOptionsChanged(result.options.providers)
      } else {
        const options = await getGlobalModelOptions()
        onOptionsChanged(options.providers ?? [])
      }
      setStatus(result.ok ? 'ok' : 'error')
      setMessage(result.ok
        ? `${selectedLabel} / ${trimmedModel} 已验证并加入模型库，可在会话右下角选择。`
        : result.message || '验证失败。')
    } catch (err) {
      setStatus('error')
      setMessage(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="mb-5 rounded-md border border-border/50 p-3">
      <div className="mb-3">
        <SettingsCategoryHeading icon={Cpu} title="提供商模型路由" />
        <p className="-mt-2 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
          Provider、Model、Base URL 会共同决定凭证池验证目标；验证通过后才会进入模型库和会话模型选择器。
        </p>
      </div>

      <div className="grid gap-2 md:grid-cols-[minmax(11rem,0.85fr)_minmax(12rem,1fr)_minmax(15rem,1.4fr)]">
        <label className="grid gap-1 text-xs font-medium">
          提供商
          <select
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
            onChange={event => {
              setProvider(event.target.value)
              setProviderTouched(true)
            }}
            value={provider}
          >
            {providerOptions.map(option => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-xs font-medium">
          模型
          <input
            autoComplete="off"
            className="h-8 min-w-0 rounded-md border border-input bg-background px-2 text-xs"
            list={modelOptions.length ? 'provider-route-models' : undefined}
            onChange={event => setModel(event.target.value)}
            placeholder="例如 gpt-5.5 或 deepseek-v4-pro"
            value={model}
          />
          {modelOptions.length > 0 && (
            <datalist id="provider-route-models">
              {modelOptions.map(id => (
                <option key={id} value={id} />
              ))}
            </datalist>
          )}
        </label>
        <label className="grid gap-1 text-xs font-medium">
          Base URL
          <input
            autoComplete="off"
            className="h-8 min-w-0 rounded-md border border-input bg-background px-2 text-xs"
            onChange={event => setBaseUrl(event.target.value)}
            placeholder="https://relay.example.com/v1 或 http://localhost:1234/v1"
            value={baseUrl}
          />
        </label>
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {ENDPOINT_PRESETS.map(preset => (
          <Button
            key={preset.id}
            onClick={() => {
              setBaseUrl(preset.baseUrl)
              setProvider(CUSTOM_PROVIDER_SLUG)
              setProviderTouched(true)
            }}
            size="sm"
            variant="outline"
          >
            {preset.name}
          </Button>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button disabled={!baseUrl.trim() || discovering || status === 'loading'} onClick={() => void discover()} size="sm" variant="outline">
          {discovering ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          发现模型
        </Button>
        <Button disabled={!provider || !model.trim() || status === 'loading'} onClick={() => void validateRoute()} size="sm">
          {status === 'loading' ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCircle2 className="size-3.5" />}
          验证并加入模型库
        </Button>
        {message && (
          <span className={cn('inline-flex min-w-0 items-center gap-1 text-xs', status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
            {status === 'error' ? <AlertCircle className="size-3.5 shrink-0" /> : status === 'ok' ? <CheckCircle2 className="size-3.5 shrink-0" /> : null}
            <span className="break-words">{message}</span>
          </span>
        )}
      </div>
    </section>
  )
}

function CredentialPoolPanel({
  catalog,
  modelProviders,
  onCatalogChanged
}: {
  catalog: ProviderCatalogEntry[]
  modelProviders: ModelOptionProvider[]
  onCatalogChanged: (providers: ProviderCatalogEntry[]) => void
}) {
  const { t } = useI18n()
  const p = t.settings.providers
  const [pool, setPool] = useState<CredentialPoolProvider[]>([])
  const [provider, setProvider] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [label, setLabel] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const providerOptions = useMemo(() => {
    const fromCatalog = catalog.map(row => ({ label: row.name || row.slug, value: row.slug }))
    const fromModels = modelProviders
      .map(row => ({ label: row.name || row.slug, value: row.slug }))
      .filter(row => row.value)
    const fromPool = pool.map(row => ({ label: row.provider, value: row.provider }))
    const fromAppImageCatalog = CREDENTIAL_POOL_PROVIDER_OPTIONS
    const seen = new Set<string>()

    return [...fromCatalog, ...fromAppImageCatalog, ...fromModels, ...fromPool]
      .filter(row => {
        if (seen.has(row.value)) {
          return false
        }
        seen.add(row.value)
        return true
      })
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [catalog, modelProviders, pool])

  const isCustomPoolProvider = provider === CUSTOM_PROVIDER_SLUG

  const refresh = async () => {
    setLoading(true)
    setError('')
    try {
      const next = await getCredentialPool()
      setPool(next.providers ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  async function addEntry() {
    if (!provider || !apiKey.trim()) {
      return
    }
    if (isCustomPoolProvider && !baseUrl.trim()) {
      setError('Base URL is required for OpenAI-compatible relay/local credentials.')
      return
    }

    setSaving(true)
    setError('')
    try {
      await addCredentialPoolEntry({
        provider,
        api_key: apiKey.trim(),
        ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
        ...(label.trim() ? { label: label.trim() } : {})
      })
      try {
        const nextCatalog = await getProviderCatalog()
        onCatalogChanged(nextCatalog.providers ?? [])
      } catch {
        // Pool refresh is still the source of truth for saved entries.
      }
      setApiKey('')
      if (isCustomPoolProvider) {
        setBaseUrl('')
      }
      setLabel('')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function removeEntry(providerId: string, index: number) {
    setError('')
    try {
      await removeCredentialPoolEntry(providerId, index)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="mt-6 border-t border-border/40 pt-5">
      <SettingsCategoryHeading icon={KeyRound} title={p.credentialPoolTitle} />
      <p className="-mt-2 mb-3 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
        {p.credentialPoolDesc}
      </p>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          className="h-8 min-w-36 rounded-md border border-input bg-background px-2 text-xs"
          onChange={event => setProvider(event.target.value)}
          value={provider}
        >
          <option value="">{p.provider}</option>
          {providerOptions.map(option => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {isCustomPoolProvider && (
          <input
            autoComplete="off"
            className="h-8 min-w-72 flex-1 rounded-md border border-input bg-background px-2 text-xs"
            onChange={event => setBaseUrl(event.target.value)}
            placeholder="Base URL, e.g. https://relay.example.com/v1 or http://localhost:1234/v1"
            value={baseUrl}
          />
        )}
        <input
          autoComplete="off"
          className="h-8 min-w-56 flex-1 rounded-md border border-input bg-background px-2 text-xs"
          onChange={event => setApiKey(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              void addEntry()
            }
          }}
          placeholder={p.apiKey}
          type="password"
          value={apiKey}
        />
        <input
          className="h-8 min-w-32 rounded-md border border-input bg-background px-2 text-xs"
          onChange={event => setLabel(event.target.value)}
          placeholder={p.label}
          value={label}
        />
        <Button disabled={!provider || !apiKey.trim() || (isCustomPoolProvider && !baseUrl.trim()) || saving} onClick={() => void addEntry()} size="sm">
          {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
          {p.add}
        </Button>
      </div>
      {isCustomPoolProvider && (
        <div className="-mt-1 mb-3 flex flex-wrap gap-1.5">
          {ENDPOINT_PRESETS.map(preset => (
            <Button
              key={preset.id}
              onClick={() => setBaseUrl(preset.baseUrl)}
              size="sm"
              variant="outline"
            >
              {preset.name}
            </Button>
          ))}
        </div>
      )}
      {error && <div className="mb-2 text-xs text-destructive">{error}</div>}
      {loading ? (
        <div className="py-3 text-xs text-muted-foreground">{p.credentialPoolLoading}</div>
      ) : pool.length === 0 ? (
        <div className="py-3 text-xs text-muted-foreground">{p.credentialPoolEmpty}</div>
      ) : (
        <div className="grid gap-1">
          {pool.map(group => (
            <div className="rounded-md border border-border/40 px-3 py-2" key={group.provider}>
              <div className="mb-1 text-xs font-medium">{providerLabel(group.provider, modelProviders)}</div>
              <div className="grid gap-1">
                {group.entries.map(entry => (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" key={entry.id ?? entry.index}>
                    <span className="font-medium text-foreground">{entry.label || `key #${entry.index}`}</span>
                    <span className="font-mono">{entry.token_preview || p.credentialPoolRedacted}</span>
                    <span>{entry.auth_type || 'api_key'}</span>
                    {entry.base_url && <span className="truncate font-mono">{entry.base_url}</span>}
                    <span>{p.requests(entry.request_count ?? 0)}</span>
                    <Button
                      className="ml-auto"
                      onClick={() => void removeEntry(group.provider, entry.index)}
                      size="sm"
                      variant="ghost"
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function ProviderCatalogPanel({ catalog }: { catalog: ProviderCatalogEntry[] }) {
  if (catalog.length === 0) {
    return null
  }

  return (
    <section className="mb-5 rounded-md border border-border/50 p-3">
      <SettingsCategoryHeading icon={Cpu} title="LLM 提供商 / API 列表" />
      <div className="mt-2 grid gap-1">
        {catalog.map(provider => (
          <div className="grid gap-1 rounded-md border border-border/30 px-2 py-1.5 text-xs md:grid-cols-[minmax(8rem,0.8fr)_minmax(10rem,1fr)_minmax(12rem,1.2fr)_minmax(8rem,0.8fr)]" key={provider.slug}>
            <div className="font-medium">{provider.name}</div>
            <div className="font-mono text-muted-foreground">{provider.slug}</div>
            <div className="truncate font-mono text-muted-foreground">{provider.base_url || 'custom base URL'}</div>
            <div className="text-muted-foreground">
              key {provider.credential_count} · 已验证模型 {provider.verified_model_count}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

export function ProvidersSettings({ view }: ProvidersSettingsProps) {
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalogEntry[]>([])
  const [modelProviders, setModelProviders] = useState<ModelOptionProvider[]>([])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const [options, catalog] = await Promise.all([getGlobalModelOptions(), getProviderCatalog()])
        if (!cancelled) {
          setModelProviders(options.providers ?? [])
          setProviderCatalog(catalog.providers ?? [])
        }
      } catch {
        // Ignore — credentials can still render from /api/env.
      }
    })()

    return () => void (cancelled = true)
  }, [])

  if (view === 'models') {
    return (
      <SettingsContent>
        <ModelLibrarySettings
          onLibraryChanged={async () => {
            try {
              const options = await getGlobalModelOptions()
              setModelProviders(options.providers ?? [])
            } catch {
              // Settings can still render; the next open refresh will retry.
            }
          }}
        />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <ProviderCatalogPanel catalog={providerCatalog} />
      <ModelRoutePanel
        catalog={providerCatalog}
        modelProviders={modelProviders}
        onDiscoveredModels={providers => setModelProviders(providers)}
        onOptionsChanged={providers => setModelProviders(providers)}
      />
      <CredentialPoolPanel
        catalog={providerCatalog}
        modelProviders={modelProviders}
        onCatalogChanged={providers => setProviderCatalog(providers)}
      />
    </SettingsContent>
  )
}

interface ProvidersSettingsProps {
  onMainModelChanged?: (provider: string, model: string) => void
  view: ProviderView
}
