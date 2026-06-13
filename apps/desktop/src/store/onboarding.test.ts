import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $desktopOnboarding,
  type DesktopOnboardingState,
  type OnboardingContext,
  completeOnboardingWithVerifiedApiKey,
  discoverOnboardingProviderModels,
  refreshOnboarding,
  requestDesktopOnboarding,
  saveOnboardingLocalEndpoint,
} from './onboarding'

function baseState(overrides: Partial<DesktopOnboardingState> = {}): DesktopOnboardingState {
  return {
    configured: false,
    flow: { status: 'idle' },
    mode: 'apikey',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    ...overrides
  }
}

function installApiMock(api: (request: { path: string }) => Promise<unknown>) {
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { api }
  })
}

function runtimeMismatchGateway(): OnboardingContext['requestGateway'] {
  return async method => {
    if (method === 'setup.status') {
      return { provider_configured: true } as never
    }

    if (method === 'setup.runtime_check') {
      return { error: 'Selected runtime is not available.', ok: false } as never
    }

    throw new Error(`unexpected gateway method: ${method}`)
  }
}

function onboardingContext(requestGateway: OnboardingContext['requestGateway']): OnboardingContext {
  return { requestGateway }
}

describe('refreshOnboarding', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  it('does not request account sign-in providers when onboarding is explicitly requested', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(baseState({ providers: [] }))
    requestDesktopOnboarding('Need provider setup')

    const ready = await refreshOnboarding(onboardingContext(runtimeMismatchGateway()))

    expect(ready).toBe(false)
    expect(api).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().providers).toEqual([])
    expect($desktopOnboarding.get().reason).toContain('Selected runtime is not available.')
    expect($desktopOnboarding.get().reason).toContain('setup.status reports configured credentials')
  })

  it('keeps the API-key onboarding mode when onboarding was not re-requested', async () => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    $desktopOnboarding.set(baseState({ providers: [] }))

    const ready = await refreshOnboarding(onboardingContext(runtimeMismatchGateway()))

    expect(ready).toBe(false)
    expect(api).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().mode).toBe('apikey')
  })
})

describe('saveOnboardingLocalEndpoint', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  function readyGateway(): OnboardingContext['requestGateway'] {
    return async method => {
      if (method === 'reload.env') {
        return {} as never
      }

      if (method === 'setup.status') {
        return { provider_configured: true } as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: true } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }
  }

  it('errors when the endpoint advertises no models (nothing to route to)', async () => {
    const calls: string[] = []
    installApiMock(async ({ path }: { path: string }) => {
      calls.push(path)

      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: [] }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const result = await saveOnboardingLocalEndpoint('http://127.0.0.1:8000/v1', {
      requestGateway: readyGateway()
    })

    expect(result.ok).toBe(false)
    expect(result.message).toContain('no models')
    // Must not attempt to persist an assignment without a model.
    expect(calls).not.toContain('/api/model/set')
  })

  it('auto-discovers the model and persists provider=custom + base_url, then finishes', async () => {
    const calls: { body?: unknown; path: string }[] = []

    const api = vi.fn(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: ['llama-3.1-8b', 'qwen2.5-7b'] }
      }

      if (path === '/api/model/set') {
        return { ok: true, provider: 'custom', model: 'llama-3.1-8b', base_url: 'http://127.0.0.1:8000/v1' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    installApiMock(api)
    const onCompleted = vi.fn()

    const result = await saveOnboardingLocalEndpoint('http://127.0.0.1:8000/v1', {
      onCompleted,
      requestGateway: readyGateway()
    })

    expect(result.ok).toBe(true)

    const assign = calls.find(c => c.path === '/api/model/set')
    expect(assign?.body).toMatchObject({
      scope: 'main',
      provider: 'custom',
      model: 'llama-3.1-8b',
      base_url: 'http://127.0.0.1:8000/v1'
    })

    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect($desktopOnboarding.get().configured).toBe(true)
  })

  it('reports the runtime reason when resolution still fails after saving', async () => {
    installApiMock(async ({ path }: { path: string }) => {
      if (path === '/api/providers/validate') {
        return { ok: true, reachable: true, message: '', models: ['llama-3.1-8b'] }
      }

      if (path === '/api/model/set') {
        return { ok: true }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const failingGateway: OnboardingContext['requestGateway'] = async method => {
      if (method === 'reload.env') {
        return {} as never
      }

      if (method === 'setup.status') {
        return { provider_configured: false } as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: false, error: 'No provider can serve the selected model.' } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }

    const result = await saveOnboardingLocalEndpoint('http://127.0.0.1:8000/v1', {
      requestGateway: failingGateway
    })

    expect(result.ok).toBe(false)
    expect(result.message).toContain('No provider can serve the selected model.')
    expect($desktopOnboarding.get().configured).not.toBe(true)
  })
})

describe('custom provider onboarding', () => {
  beforeEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
  })

  afterEach(() => {
    window.localStorage.clear()
    $desktopOnboarding.set(baseState())
    vi.restoreAllMocks()
  })

  const customProvider = {
    api_key_env_vars: ['CUSTOM_API_KEY'],
    auth_type: 'api_key',
    base_url: '',
    base_url_env_var: 'OPENAI_BASE_URL',
    credential_count: 0,
    key_env: 'CUSTOM_API_KEY',
    name: 'OpenAI 兼容 / 中转站 / 本地',
    slug: 'custom',
    source: 'custom',
    supports_model_discovery: true,
    verified_model_count: 0
  }

  function readyGateway(): OnboardingContext['requestGateway'] {
    return async method => {
      if (method === 'reload.env') {
        return {} as never
      }

      if (method === 'setup.status') {
        return { provider_configured: true } as never
      }

      if (method === 'setup.runtime_check') {
        return { ok: true } as never
      }

      throw new Error(`unexpected gateway method: ${method}`)
    }
  }

  it('discovers custom/local models with Base URL only and no credential write', async () => {
    const calls: { body?: unknown; path: string }[] = []
    installApiMock(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/models/discover') {
        return { ok: true, provider: 'custom', base_url: 'http://127.0.0.1:8000/v1', models: ['local-model'], checked: [], message: 'ok' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const result = await discoverOnboardingProviderModels({
      provider: customProvider,
      apiKey: '',
      baseUrl: 'http://127.0.0.1:8000/v1'
    })

    expect(result.ok).toBe(true)
    expect(result.credentialSaved).toBe(false)
    expect(result.models).toEqual(['local-model'])
    expect(calls).toEqual([
      {
        path: '/api/models/discover',
        body: { provider: 'custom', base_url: 'http://127.0.0.1:8000/v1' }
      }
    ])
  })

  it('validates and assigns a custom/local model with Base URL only', async () => {
    const calls: { body?: unknown; path: string }[] = []
    installApiMock(async ({ body, path }: { body?: unknown; path: string }) => {
      calls.push({ body, path })

      if (path === '/api/models/route/validate') {
        return { ok: true, provider: 'custom', model: 'local-model', message: 'ok' }
      }

      if (path === '/api/model/set') {
        return { ok: true, provider: 'custom', model: 'local-model', base_url: 'http://127.0.0.1:8000/v1' }
      }

      throw new Error(`unexpected api path: ${path}`)
    })

    const result = await completeOnboardingWithVerifiedApiKey(
      {
        provider: customProvider,
        apiKey: '',
        baseUrl: 'http://127.0.0.1:8000/v1',
        model: 'local-model',
        credentialAlreadySaved: false
      },
      { requestGateway: readyGateway() }
    )

    expect(result.ok).toBe(true)
    expect(calls.map(call => call.path)).toEqual(['/api/models/route/validate', '/api/model/set'])
    expect(calls[0].body).toMatchObject({
      provider: 'custom',
      model: 'local-model',
      base_url: 'http://127.0.0.1:8000/v1'
    })
    expect(calls[1].body).toMatchObject({
      scope: 'main',
      provider: 'custom',
      model: 'local-model',
      base_url: 'http://127.0.0.1:8000/v1'
    })
  })
})
