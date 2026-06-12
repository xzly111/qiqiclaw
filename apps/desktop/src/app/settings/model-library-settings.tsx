import { useCallback, useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  addSavedModel,
  getGlobalModelOptions,
  listSavedModels,
  removeSavedModel,
  updateSavedModel,
  validateSavedModel,
  validateProviderCredential
} from '@/hermes'
import type { ModelOptionProvider, SavedModel } from '@/types/hermes'
import { AlertCircle, CheckCircle2, Cpu, Loader2, Plus, RefreshCw, Search, Trash2 } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { CONTROL_TEXT } from './constants'
import { LoadingState, SectionHeading } from './primitives'
import {
  CUSTOM_PROVIDER_LABEL,
  CUSTOM_PROVIDER_SLUG,
  ENDPOINT_PRESETS,
  customEnvKeyForBaseUrl,
  detectProviderFromBaseUrl,
  providerLabel,
  withCustomProvider
} from './provider-options'

type DiscoveryStatus = 'idle' | 'loading' | 'ok' | 'error'

interface ModelLibrarySettingsProps {
  onLibraryChanged?: () => void
}

export function ModelLibrarySettings({ onLibraryChanged }: ModelLibrarySettingsProps) {
  const [models, setModels] = useState<SavedModel[]>([])
  const [providers, setProviders] = useState<ModelOptionProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<SavedModel | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [validatingId, setValidatingId] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [library, options] = await Promise.all([listSavedModels(), getGlobalModelOptions()])
      setModels(library.models ?? [])
      setProviders(withCustomProvider(options.providers ?? []))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return models
    return models.filter(model =>
      `${model.name} ${model.provider} ${model.model} ${model.base_url}`.toLowerCase().includes(q)
    )
  }, [models, search])

  const openAdd = () => {
    setEditing(null)
    setModalOpen(true)
  }

  const openEdit = (model: SavedModel) => {
    setEditing(model)
    setModalOpen(true)
  }

  const remove = async (id: string) => {
    setError('')
    try {
      await removeSavedModel(id)
      await refresh()
      onLibraryChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const validate = async (model: SavedModel) => {
    setError('')
    setValidatingId(model.id)
    try {
      await validateSavedModel(model.id)
      await refresh()
      onLibraryChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setValidatingId('')
    }
  }

  if (loading) {
    return <LoadingState label="Loading model library..." />
  }

  return (
    <div className="grid gap-4">
      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <SectionHeading icon={Cpu} title="模型库" />
            <p className="mt-1 text-xs text-muted-foreground">
              这些模型会出现在会话右下角模型选择器中；API 是否可用通过对应凭证池、Base URL 和模型发现验证。
            </p>
          </div>
          <Button onClick={openAdd} size="sm">
            <Plus className="size-3.5" />
            添加模型
          </Button>
        </div>

        <div className="mb-3 flex items-center gap-2 rounded-md border border-border/50 px-2">
          <Search className="size-3.5 text-muted-foreground" />
          <input
            className="h-8 min-w-0 flex-1 bg-transparent text-xs outline-none"
            onChange={event => setSearch(event.target.value)}
            placeholder="搜索模型、提供商、Base URL"
            value={search}
          />
        </div>

        {error && <div className="mb-2 text-xs text-destructive">{error}</div>}

        {filtered.length === 0 ? (
          <div className="rounded-md border border-dashed border-border/50 px-4 py-8 text-center text-xs text-muted-foreground">
            暂无模型库条目。
          </div>
        ) : (
          <div className="grid gap-2">
            {filtered.map(model => (
              <div
                className="rounded-md border border-border/50 px-3 py-2 text-left transition hover:bg-accent/40"
                key={model.id}
                onClick={() => openEdit(model)}
                role="button"
                tabIndex={0}
              >
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{model.name}</span>
                      <Badge variant="outline">{providerLabel(model.provider, providers)}</Badge>
                      {model.verified ? (
                        <Badge className="gap-1 border-emerald-500/40 bg-emerald-500/10 text-emerald-600" variant="outline">
                          <CheckCircle2 className="size-3" />
                          已验证
                        </Badge>
                      ) : (
                        <Badge className="gap-1 border-amber-500/40 bg-amber-500/10 text-amber-600" variant="outline">
                          <AlertCircle className="size-3" />
                          未验证
                        </Badge>
                      )}
                    </div>
                    <div className="mt-1 font-mono text-xs text-muted-foreground">{model.model}</div>
                    {model.base_url && <div className="mt-1 truncate font-mono text-[0.68rem] text-muted-foreground">{model.base_url}</div>}
                    {model.verification_message && (
                      <div className="mt-1 text-[0.68rem] text-muted-foreground">
                        {model.verification_message}
                        {model.credential_index ? ` · 凭证 #${model.credential_index}` : ''}
                      </div>
                    )}
                  </div>
                  <Button
                    disabled={validatingId === model.id}
                    onClick={event => {
                      event.stopPropagation()
                      void validate(model)
                    }}
                    size="sm"
                    variant="outline"
                  >
                    {validatingId === model.id ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                    验证
                  </Button>
                  <Button
                    onClick={event => {
                      event.stopPropagation()
                      void remove(model.id)
                    }}
                    size="sm"
                    variant="ghost"
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <ModelLibraryDialog
        editing={editing}
        onChanged={async () => {
          await refresh()
          onLibraryChanged?.()
        }}
        onOpenChange={setModalOpen}
        open={modalOpen}
        providers={providers}
      />
    </div>
  )
}

function ModelLibraryDialog({
  editing,
  onChanged,
  onOpenChange,
  open,
  providers
}: {
  editing: SavedModel | null
  onChanged: () => Promise<void>
  onOpenChange: (open: boolean) => void
  open: boolean
  providers: ModelOptionProvider[]
}) {
  const [name, setName] = useState('')
  const [provider, setProvider] = useState(CUSTOM_PROVIDER_SLUG)
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('http://localhost:1234/v1')
  const [apiKey, setApiKey] = useState('')
  const [providerTouched, setProviderTouched] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus>('idle')
  const [discoveryMessage, setDiscoveryMessage] = useState('')
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])

  useEffect(() => {
    if (!open) return
    setName(editing?.name ?? '')
    setProvider(editing?.provider ?? CUSTOM_PROVIDER_SLUG)
    setModel(editing?.model ?? '')
    setBaseUrl(editing?.base_url || 'http://localhost:1234/v1')
    setApiKey('')
    setProviderTouched(!!editing)
    setError('')
    setDiscoveryStatus('idle')
    setDiscoveryMessage('')
    setDiscoveredModels([])
  }, [editing, open])

  useEffect(() => {
    if (!open || providerTouched || !baseUrl.trim()) return
    const detected = detectProviderFromBaseUrl(baseUrl)
    if (detected && detected !== provider) {
      setProvider(detected)
    }
  }, [baseUrl, open, provider, providerTouched])

  const providerOptions = providers.length ? providers : withCustomProvider([])
  const providerModels = providers.find(row => row.slug === provider)?.models ?? []
  const modelOptions = [...new Set([...providerModels, ...discoveredModels, model].filter(Boolean))]
  const isCustom = provider === CUSTOM_PROVIDER_SLUG

  const discover = async () => {
    const url = baseUrl.trim()
    if (!url) {
      setDiscoveryStatus('error')
      setDiscoveryMessage('Base URL is required before model discovery.')
      return
    }

    setDiscoveryStatus('loading')
    setDiscoveryMessage('')
    try {
      const result = await validateProviderCredential('OPENAI_BASE_URL', url)
      if (!result.reachable) {
        setDiscoveryStatus('error')
        setDiscoveryMessage(result.message || `Could not reach ${url}.`)
        return
      }
      const models = result.models ?? []
      setDiscoveredModels(models)
      setDiscoveryStatus('ok')
      setDiscoveryMessage(models.length ? `${models.length} models discovered.` : 'Endpoint is reachable, but no models were advertised.')
      if (!model && models[0]) setModel(models[0])
    } catch (err) {
      setDiscoveryStatus('error')
      setDiscoveryMessage(err instanceof Error ? err.message : String(err))
    }
  }

  const save = async () => {
    const trimmedModel = model.trim()
    const trimmedProvider = provider.trim()
    const trimmedBaseUrl = baseUrl.trim()
    const trimmedName = name.trim() || trimmedModel

    if (!trimmedProvider || !trimmedModel) {
      setError('Provider and model ID are required.')
      return
    }
    if (trimmedProvider === CUSTOM_PROVIDER_SLUG && !trimmedBaseUrl) {
      setError('Base URL is required for OpenAI-compatible relay/local models.')
      return
    }

    setSaving(true)
    setError('')
    try {
      if (editing) {
        await updateSavedModel(editing.id, {
          name: trimmedName,
          provider: trimmedProvider,
          model: trimmedModel,
          base_url: trimmedBaseUrl
        })
      } else {
        await addSavedModel({
          name: trimmedName,
          provider: trimmedProvider,
          model: trimmedModel,
          base_url: trimmedBaseUrl,
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {})
        })
      }
      await onChanged()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? '编辑模型' : '添加模型'}</DialogTitle>
        </DialogHeader>

        <div className="grid gap-3">
          <label className="grid gap-1.5 text-xs font-medium">
            显示名称
            <Input autoFocus className={CONTROL_TEXT} onChange={event => setName(event.target.value)} placeholder="例如 Qwen Relay" value={name} />
          </label>

          <label className="grid gap-1.5 text-xs font-medium">
            提供商
            <Select
              onValueChange={value => {
                setProvider(value)
                setProviderTouched(true)
              }}
              value={provider}
            >
              <SelectTrigger className={cn('w-full', CONTROL_TEXT)}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {providerOptions.map(option => (
                  <SelectItem key={option.slug} value={option.slug}>
                    {option.slug === CUSTOM_PROVIDER_SLUG ? CUSTOM_PROVIDER_LABEL : option.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>

          <label className="grid gap-1.5 text-xs font-medium">
            模型 ID
            <Input
              autoComplete="off"
              className={CONTROL_TEXT}
              list={modelOptions.length ? 'model-library-discovered' : undefined}
              onChange={event => setModel(event.target.value)}
              placeholder="例如 qwen-plus 或 openai/gpt-4.1"
              value={model}
            />
            {modelOptions.length > 0 && (
              <datalist id="model-library-discovered">
                {modelOptions.map(id => (
                  <option key={id} value={id} />
                ))}
              </datalist>
            )}
          </label>

          <label className="grid gap-1.5 text-xs font-medium">
            Base URL
            <Input
              autoComplete="off"
              className={CONTROL_TEXT}
              onChange={event => setBaseUrl(event.target.value)}
              placeholder="http://localhost:1234/v1 或 https://relay.example.com/v1"
              value={baseUrl}
            />
          </label>

          <div className="flex flex-wrap gap-1.5">
            {ENDPOINT_PRESETS.map(preset => (
              <Button
                key={preset.id}
                onClick={() => {
                  setProvider(CUSTOM_PROVIDER_SLUG)
                  setProviderTouched(true)
                  setBaseUrl(preset.baseUrl)
                }}
                size="sm"
                variant="outline"
              >
                {preset.name}
              </Button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!baseUrl.trim() || discoveryStatus === 'loading'} onClick={() => void discover()} size="sm" variant="textStrong">
              {discoveryStatus === 'loading' ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
              检测/发现模型
            </Button>
            {discoveryMessage && (
              <span className={cn('text-xs', discoveryStatus === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
                {discoveryMessage}
              </span>
            )}
          </div>

          {!editing && isCustom && (
            <label className="grid gap-1.5 text-xs font-medium">
              API Key（可选）
              <Input
                autoComplete="off"
                className={CONTROL_TEXT}
                onChange={event => setApiKey(event.target.value)}
                placeholder={`保存到 ${customEnvKeyForBaseUrl(baseUrl)}`}
                type="password"
                value={apiKey}
              />
            </label>
          )}

          {error && <div className="text-xs text-destructive">{error}</div>}
        </div>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            取消
          </Button>
          <Button disabled={saving} onClick={() => void save()}>
            {saving && <Loader2 className="size-3.5 animate-spin" />}
            {editing ? '更新' : '添加模型'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
