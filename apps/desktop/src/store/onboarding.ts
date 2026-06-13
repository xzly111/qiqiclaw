import { atom } from 'nanostores'

import {
  addCredentialPoolEntry,
  discoverProviderModels,
  getGlobalModelOptions,
  getRecommendedDefaultModel,
  getProviderCatalog,
  setEnvVar,
  setModelAssignment,
  validateModelRoute,
  validateProviderCredential
} from '@/hermes'
import { evaluateRuntimeReadiness, type RuntimeReadinessResult } from '@/lib/runtime-readiness'
import { notify, notifyError } from '@/store/notifications'
import type { ModelOptionProvider, ProviderCatalogEntry } from '@/types/hermes'

export type OnboardingMode = 'apikey'

export type OnboardingFlow =
  | { status: 'idle' }
  | {
      // After successful credential acquisition, before completing onboarding:
      // show the user which model they're getting and let them change it.
      currentModel: string
      label: string
      providerSlug: string
      saving: boolean
      status: 'confirming_model'
    }
  | { message: string; status: 'error' }

export interface DesktopOnboardingState {
  /** null until the first runtime check resolves. Seeded from localStorage so
   *  returning users skip the boot overlay entirely instead of flashing it
   *  every reload. */
  configured: boolean | null
  flow: OnboardingFlow
  mode: OnboardingMode
  providers: null | []
  reason: null | string
  requested: boolean
  /** True when the user explicitly chose "I'll choose a provider later" on the
   *  first-run picker. Persisted to localStorage so the blocking overlay never
   *  re-nags on subsequent launches — the user can connect a provider any time
   *  from Settings → Providers (or the model picker's "Add provider"). Distinct
   *  from `configured`: the app still has no usable provider, so chat won't work
   *  until one is connected; we just stop forcing the choice up front. */
  firstRunSkipped: boolean
  /** True when the user explicitly opened the provider selector to add /
   *  switch providers from an already-configured app (e.g. via the model
   *  picker's "Add provider" button). Forces the overlay to show the picker
   *  even when configured === true, and adds a close affordance. */
  manual: boolean
}

export interface OnboardingContext {
  onCompleted?: () => void
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export interface OnboardingLlmProviderSetupInput {
  apiKey: string
  baseUrl?: string
  credentialAlreadySaved?: boolean
  model: string
  provider: ProviderCatalogEntry
}

const CONFIGURED_CACHE_KEY = 'qiqiclaw-desktop-onboarded-v1'
const SKIP_CACHE_KEY = 'qiqiclaw-onboarding-skipped-v1'
export const DEFAULT_ONBOARDING_REASON = 'No inference provider is configured.'
export const DEFAULT_MANUAL_ONBOARDING_REASON = 'Add or switch inference provider.'

function readCachedConfigured(): boolean | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return window.localStorage.getItem(CONFIGURED_CACHE_KEY) === '1' ? true : null
  } catch {
    return null
  }
}

function writeCachedConfigured(value: boolean) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (value) {
      window.localStorage.setItem(CONFIGURED_CACHE_KEY, '1')
    } else {
      window.localStorage.removeItem(CONFIGURED_CACHE_KEY)
    }
  } catch {
    // localStorage unavailable — degrade silently.
  }
}

function readCachedSkipped(): boolean {
  if (typeof window === 'undefined') {
    return false
  }

  try {
    return window.localStorage.getItem(SKIP_CACHE_KEY) === '1'
  } catch {
    return false
  }
}

function writeCachedSkipped(value: boolean) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (value) {
      window.localStorage.setItem(SKIP_CACHE_KEY, '1')
    } else {
      window.localStorage.removeItem(SKIP_CACHE_KEY)
    }
  } catch {
    // localStorage unavailable — degrade silently.
  }
}

const INITIAL: DesktopOnboardingState = {
  configured: readCachedConfigured(),
  flow: { status: 'idle' },
  mode: 'apikey',
  providers: null,
  reason: null,
  requested: false,
  firstRunSkipped: readCachedSkipped(),
  manual: false
}

export const $desktopOnboarding = atom<DesktopOnboardingState>(INITIAL)

let pollTimer: number | null = null

const errMessage = (e: unknown) => (e instanceof Error ? e.message : String(e))

const patch = (update: Partial<DesktopOnboardingState>) =>
  $desktopOnboarding.set({ ...$desktopOnboarding.get(), ...update })

const setFlow = (flow: OnboardingFlow) =>
  patch(flow.status === 'idle' ? { flow } : { flow, reason: null })

function clearPoll() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function checkRuntime(ctx: OnboardingContext): Promise<RuntimeReadinessResult> {
  return evaluateRuntimeReadiness(ctx.requestGateway, {
    defaultReason: DEFAULT_ONBOARDING_REASON,
    unknownReady: false
  })
}

function notifyReady(provider: string) {
  notify({ kind: 'success', title: 'QiQiClaw is ready', message: `${provider} connected.` })
}

// Human-friendly labels for tools auto-routed through the Nous Tool Gateway,
// mirroring hermes_cli/nous_subscription._GATEWAY_TOOL_LABELS so the GUI and
// CLI describe the same thing.
const GATEWAY_TOOL_LABELS: Record<string, string> = {
  browser: 'browser automation',
  image_gen: 'image generation',
  tts: 'text-to-speech',
  video_gen: 'video generation',
  web: 'web search & extract'
}

// When switching to Nous auto-routes unconfigured tools through the Tool
// Gateway, tell the user which ones — same information the CLI prints. Silent
// when nothing changed (subscriber already configured, has own keys, etc.).
function notifyGatewayTools(tools: string[] | undefined) {
  if (!tools || tools.length === 0) {
    return
  }

  const labels = tools.map(t => GATEWAY_TOOL_LABELS[t] ?? t)
  const list = labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`

  notify({
    durationMs: 8000,
    kind: 'info',
    message: `${list} now run through your Nous subscription — no separate API keys needed.`,
    title: 'Tool Gateway enabled'
  })
}

// After credentials are persisted, ask the backend which provider+models
// are now authenticated. Pick the first curated model for the matching
// provider as a sensible default, persist it via /api/model/set, and
// transition to the model-confirmation step. If anything goes wrong
// fetching options (no providers returned, network error), the caller
// falls through to completing onboarding without showing the confirm
// card — the user gets the undefined-model auto-selection behaviour
// we had before, which works but is surprising. The confirm step is
// opportunistic polish, not a hard requirement for onboarding.
async function fetchProviderDefaultModel(
  preferredSlugs: string[]
): Promise<null | { providerSlug: string; defaultModel: string }> {
  let options

  try {
    options = await getGlobalModelOptions()
  } catch {
    return null
  }

  const providers = options?.providers ?? []

  if (providers.length === 0) {
    return null
  }

  // Try each preferred slug (lowercased), fall back to the first provider
  // returned (model.options orders by recency / authenticated state, so
  // the just-authenticated provider is usually first anyway).
  const lower = preferredSlugs.map(s => s.toLowerCase())

  const matched =
    providers.find((p: ModelOptionProvider) => lower.includes(String(p.slug).toLowerCase())) ?? providers[0]

  const models = matched.models ?? []

  if (models.length === 0) {
    return null
  }

  // Prefer the backend's recommended default — it mirrors the curation
  // `hermes model` does (for Nous it honors the user's free/paid tier, so a
  // free user gets a free model rather than a paid default like opus). Fall
  // back to the first curated model if the endpoint can't resolve one.
  let defaultModel = String(models[0])

  try {
    const recommended = await getRecommendedDefaultModel(String(matched.slug))

    if (recommended.model && models.map(String).includes(recommended.model)) {
      defaultModel = recommended.model
    } else if (recommended.model) {
      // Recommended model isn't in the curated options list (e.g. a Portal
      // free-recommendation the picker list didn't include); trust it anyway.
      defaultModel = recommended.model
    }
  } catch {
    // Endpoint unavailable — keep models[0]. Non-fatal: the confirm card still
    // shows and the user can change it.
  }

  return {
    providerSlug: String(matched.slug),
    defaultModel
  }
}

// After API-key success: reload the backend env, verify runtime, then either
// show the model-confirm step or fall straight through to completion if we
// can't determine a default.
async function completeWithModelConfirm(
  ctx: OnboardingContext,
  providerLabel: string,
  preferredSlugs: string[],
  onFail: (reason: null | string) => void,
  // When true, a failing runtime check no longer blocks progression.
  ignoreRuntimeGate = false
) {
  await ctx.requestGateway('reload.env').catch(() => undefined)
  const runtime = await checkRuntime(ctx)

  if (!runtime.ready && !ignoreRuntimeGate) {
    onFail(runtime.reason)

    return
  }

  const defaults = await fetchProviderDefaultModel(preferredSlugs)

  if (!defaults) {
    // Couldn't get a sensible default — proceed without confirm step.
    notifyReady(providerLabel)
    completeDesktopOnboarding()
    ctx.onCompleted?.()

    return
  }

  // Persist the default model BEFORE showing the confirm card so that:
  // (1) "current default: X" shown in the UI is what's actually written
  //     to config — no lying.
  // (2) If the user clicks "Start chatting" without changing anything,
  //     no extra write is needed.
  // (3) If they bail out (e.g., refresh the page), they still end up
  //     with a working config, not an empty-model fallback.
  try {
    const res = await setModelAssignment({
      scope: 'main',
      provider: defaults.providerSlug,
      model: defaults.defaultModel
    })

    notifyGatewayTools(res.gateway_tools)
  } catch {
    // Persistence failed — still show the confirm card so the user can
    // pick something explicitly. The backend will pick its own default
    // at chat time if we end up never persisting.
  }

  setFlow({
    status: 'confirming_model',
    providerSlug: defaults.providerSlug,
    currentModel: defaults.defaultModel,
    label: providerLabel,
    saving: false
  })
}

function providerResolutionFailure(reason: null | string) {
  const detail = reason?.trim()

  return detail
    ? `Connected, but QiQiClaw still cannot resolve a usable provider. ${detail}`
    : 'Connected, but QiQiClaw still cannot resolve a usable provider.'
}

export function requestDesktopOnboarding(reason = DEFAULT_ONBOARDING_REASON) {
  patch({ reason: reason.trim() || DEFAULT_ONBOARDING_REASON, requested: true })
}

// Open the onboarding provider selector on demand from an already-configured
// app — e.g. the model picker's "Add provider" button. The desktop onboarding
// surface is intentionally API-key/provider-route only; account sign-in is not
// part of the first-run or add-provider flow.
export function startManualOnboarding(reason: null | string = DEFAULT_MANUAL_ONBOARDING_REASON) {
  patch({
    manual: true,
    mode: 'apikey',
    requested: true,
    providers: [],
    // `null` opts out of the prompt banner entirely.
    reason: reason ? reason.trim() || DEFAULT_ONBOARDING_REASON : null,
    flow: { status: 'idle' }
  })
}

// Dismiss a manually-opened provider selector without touching the existing
// (working) configuration. Only valid in the manual path — the unconfigured
// first-run flow has no close affordance because the app can't run yet.
export function closeManualOnboarding() {
  patch({ manual: false, requested: false, flow: { status: 'idle' } })
}

export function completeDesktopOnboarding() {
  clearPoll()
  writeCachedConfigured(true)
  // A real provider is now connected, so any earlier "choose later" skip is
  // moot — clear it so the flag never lingers in a configured install.
  writeCachedSkipped(false)
  $desktopOnboarding.set({
    configured: true,
    flow: { status: 'idle' },
    mode: 'apikey',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false
  })
}

export function dismissFirstRunOnboarding() {
  clearPoll()
  writeCachedSkipped(true)
  patch({ firstRunSkipped: true, requested: false, manual: false, flow: { status: 'idle' } })
}

export function setOnboardingMode(mode: OnboardingMode) {
  patch({ mode })
}

export async function refreshOnboarding(ctx: OnboardingContext) {
  // Manual mode (user opened the selector from a working app): never
  // auto-dismiss on runtime-ready — the whole point is to let them add /
  // switch a provider while already configured. Just ensure the provider
  // list is loaded and show the picker.
  if ($desktopOnboarding.get().manual) {
    patch({ mode: 'apikey', providers: [] })

    return false
  }

  const runtime = await checkRuntime(ctx)

  if (runtime.ready) {
    completeDesktopOnboarding()
    ctx.onCompleted?.()

    return true
  }

  const state = $desktopOnboarding.get()
  const reason = runtime.reason || state.reason || DEFAULT_ONBOARDING_REASON

  writeCachedConfigured(false)
  patch({ configured: false, reason })

  if (state.providers !== null && !state.requested) {
    return false
  }

  patch({ mode: 'apikey', providers: [] })

  return false
}

export function cancelOnboardingFlow() {
  clearPoll()

  setFlow({ status: 'idle' })
}

export async function saveOnboardingApiKey(envKey: string, value: string, label: string, ctx: OnboardingContext) {
  const trimmed = value.trim()

  if (!trimmed) {
    return { ok: false, message: 'Enter a value first.' }
  }

  // The "Local / custom endpoint" option carries a base URL, not an API key.
  // It must be wired into config (provider=custom + base_url + model), not
  // dropped into .env — runtime resolution ignores OPENAI_BASE_URL.
  if (envKey === 'OPENAI_BASE_URL') {
    return saveOnboardingLocalEndpoint(trimmed, ctx)
  }

  // No key validation here on purpose: we previously live-probed the key and
  // hard-blocked on a runtime check after saving, which rejected too many
  // legitimate users (corporate proxies, regional blocks, flaky/rate-limited
  // provider probes, self-hosted endpoints). We now save the value as-is and
  // let the user proceed; an actually-bad key surfaces later at chat time.
  try {
    await setEnvVar(envKey, trimmed)
    // For API-key flows we don't have a definitive provider id (the
    // user picked which API key they're entering, but the corresponding
    // backend slug — e.g. OPENROUTER_API_KEY → "openrouter" — is the
    // env-key prefix stripped). Pass a couple of likely candidates;
    // fetchProviderDefaultModel falls back to the first authenticated
    // provider returned by /api/model/options if none match.
    const slugCandidates = [envKey.replace(/_API_KEY$/, '').toLowerCase(), label.toLowerCase()]
    // ignoreRuntimeGate=true: never block onboarding on the runtime check.
    await completeWithModelConfirm(ctx, label, slugCandidates, () => undefined, true)

    return { ok: true }
  } catch (error) {
    notifyError(error, `Could not save ${label}`)

    return { ok: false, message: errMessage(error) }
  }
}

export async function loadOnboardingLlmProviders(): Promise<ProviderCatalogEntry[]> {
  const catalog = await getProviderCatalog()
  const preferred = ['openrouter', 'deepseek', 'alibaba', 'gemini', 'xai', 'openai', 'anthropic', 'custom']

  return (catalog.providers ?? [])
    .filter(provider => provider.auth_type === 'api_key' && Boolean(provider.key_env || provider.slug === 'custom'))
    .sort((a, b) => {
      const ai = preferred.indexOf(a.slug)
      const bi = preferred.indexOf(b.slug)

      if (ai !== -1 || bi !== -1) {
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
      }

      return (a.name || a.slug).localeCompare(b.name || b.slug)
    })
}

export async function discoverOnboardingProviderModels(input: {
  apiKey: string
  baseUrl?: string
  provider: ProviderCatalogEntry
}): Promise<{ credentialSaved: boolean; message: string; models: string[]; ok: boolean }> {
  const apiKey = input.apiKey.trim()
  const baseUrl = input.baseUrl?.trim() ?? ''
  const providerSlug = input.provider.slug

  if (!providerSlug || !apiKey) {
    return { ok: false, credentialSaved: false, message: '请选择提供商并输入 API Key。', models: [] }
  }

  if (providerSlug === 'custom' && !baseUrl) {
    return { ok: false, credentialSaved: false, message: 'OpenAI 兼容 / 中转站 / 本地需要填写 Base URL。', models: [] }
  }

  let credentialSaved = false

  try {
    await addCredentialPoolEntry({
      provider: providerSlug,
      api_key: apiKey,
      ...(baseUrl ? { base_url: baseUrl } : {}),
      label: 'desktop onboarding'
    })
    credentialSaved = true

    const discovered = await discoverProviderModels({
      provider: providerSlug,
      ...(baseUrl ? { base_url: baseUrl } : {})
    })

    if (!discovered.ok) {
      return {
        ok: false,
        credentialSaved,
        message: discovered.message || '没有发现可用模型。',
        models: discovered.models ?? []
      }
    }

    return {
      ok: true,
      credentialSaved,
      message: discovered.message || `发现 ${discovered.models?.length ?? 0} 个模型。`,
      models: discovered.models ?? []
    }
  } catch (error) {
    return { ok: false, credentialSaved, message: errMessage(error), models: [] }
  }
}

export async function completeOnboardingWithVerifiedApiKey(
  input: OnboardingLlmProviderSetupInput,
  ctx: OnboardingContext
): Promise<{ message?: string; ok: boolean }> {
  const apiKey = input.apiKey.trim()
  const model = input.model.trim()
  const baseUrl = input.baseUrl?.trim() ?? ''
  const providerSlug = input.provider.slug
  const providerLabel = input.provider.name || providerSlug

  if (!providerSlug || !apiKey || !model) {
    return { ok: false, message: '请选择提供商，输入 API Key，并选择或填写模型。' }
  }

  if (providerSlug === 'custom' && !baseUrl) {
    return { ok: false, message: 'OpenAI 兼容 / 中转站 / 本地需要填写 Base URL。' }
  }

  try {
    if (!input.credentialAlreadySaved) {
      await addCredentialPoolEntry({
        provider: providerSlug,
        api_key: apiKey,
        ...(baseUrl ? { base_url: baseUrl } : {}),
        label: 'desktop onboarding'
      })
    }

    const validation = await validateModelRoute({
      provider: providerSlug,
      model,
      ...(baseUrl ? { base_url: baseUrl } : {}),
      name: model
    })

    if (!validation.ok) {
      return { ok: false, message: validation.message || '模型验证失败。' }
    }

    const assignment = await setModelAssignment({
      scope: 'main',
      provider: providerSlug,
      model,
      ...(baseUrl ? { base_url: baseUrl } : {})
    })
    notifyGatewayTools(assignment.gateway_tools)
    await ctx.requestGateway('reload.env').catch(() => undefined)
    const runtime = await checkRuntime(ctx)

    if (!runtime.ready) {
      return { ok: false, message: providerResolutionFailure(runtime.reason) }
    }

    notifyReady(providerLabel)
    completeDesktopOnboarding()
    ctx.onCompleted?.()

    return { ok: true }
  } catch (error) {
    notifyError(error, `Could not configure ${providerLabel}`)

    return { ok: false, message: errMessage(error) }
  }
}

// Configure a local / self-hosted OpenAI-compatible endpoint (vLLM, llama.cpp,
// Ollama, …). Unlike API-key providers, a local endpoint is defined by its URL
// and usually needs NO key. The runtime resolver reads model.base_url from
// config (it ignores the OPENAI_BASE_URL env var), so we persist
// provider=custom + base_url + model via /api/model/set rather than dropping an
// env var that resolution never consults.
//
// The model is auto-discovered from the endpoint's /v1/models (surfaced by the
// validate probe) so the user only has to paste a URL — no extra UI field.
//
// We deliberately don't route through completeWithModelConfirm: that path
// re-assigns the model from /api/model/options WITHOUT a base_url, which would
// wipe the base_url we just wrote. We have a concrete model already, so we
// verify the runtime directly and finish.
export async function saveOnboardingLocalEndpoint(baseUrl: string, ctx: OnboardingContext) {
  const url = baseUrl.trim()

  if (!url) {
    return { ok: false, message: 'Enter the endpoint URL first.' }
  }

  // Probe connectivity + discover the served models. Any HTTP response proves
  // the endpoint is up; an unreachable probe hard-blocks because we can't
  // resolve a model to route to.
  let model = ''

  try {
    const probe = await validateProviderCredential('OPENAI_BASE_URL', url)

    if (!probe.ok && probe.reachable) {
      return { ok: false, message: probe.message || 'Could not reach that endpoint.' }
    }

    if (!probe.reachable) {
      return { ok: false, message: probe.message || `Could not reach ${url}.` }
    }

    model = (probe.models?.[0] ?? '').trim()
  } catch {
    return { ok: false, message: `Could not reach ${url}.` }
  }

  if (!model) {
    return {
      ok: false,
      message: `Connected to ${url}, but it advertised no models at /v1/models. Start a model on that endpoint and try again.`
    }
  }

  try {
    await setModelAssignment({ scope: 'main', provider: 'custom', model, base_url: url })
    await ctx.requestGateway('reload.env').catch(() => undefined)

    const runtime = await checkRuntime(ctx)

    if (!runtime.ready) {
      const detail = (runtime.reason ?? '').trim()

      return { ok: false, message: detail || `Saved, but QiQiClaw still cannot reach ${url}.` }
    }

    notifyReady('Local / custom endpoint')
    completeDesktopOnboarding()
    ctx.onCompleted?.()

    return { ok: true }
  } catch (error) {
    notifyError(error, 'Could not save local endpoint')

    return { ok: false, message: errMessage(error) }
  }
}

// User picked a different model from the dropdown on the confirm card.
// Persists immediately so the displayed value is always what's on disk.
export async function setOnboardingModel(model: string) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'confirming_model') {
    return
  }

  // Optimistic update so the dropdown feels instant; revert on failure.
  const previous = flow.currentModel
  setFlow({ ...flow, currentModel: model, saving: true })

  try {
    await setModelAssignment({
      scope: 'main',
      provider: flow.providerSlug,
      model
    })
    const current = $desktopOnboarding.get().flow

    if (current.status === 'confirming_model') {
      setFlow({ ...current, currentModel: model, saving: false })
    }
  } catch (error) {
    notifyError(error, 'Could not change model')
    const current = $desktopOnboarding.get().flow

    if (current.status === 'confirming_model') {
      setFlow({ ...current, currentModel: previous, saving: false })
    }
  }
}

// User clicked "Start chatting" on the confirm card. Finalizes onboarding
// — the model was already persisted by completeWithModelConfirm (or by
// setOnboardingModel if they changed it), so all that's left is to mark
// onboarding done and unblock the rest of the app.
export function confirmOnboardingModel(ctx: OnboardingContext) {
  const { flow } = $desktopOnboarding.get()

  if (flow.status !== 'confirming_model') {
    return
  }

  // No success toast here: the confirm-model screen already showed "<provider>
  // connected." notifyReady is reserved for completion paths that SKIP this
  // screen (no-default fallthrough, local endpoint) so feedback isn't lost.
  completeDesktopOnboarding()
  ctx.onCompleted?.()
}
