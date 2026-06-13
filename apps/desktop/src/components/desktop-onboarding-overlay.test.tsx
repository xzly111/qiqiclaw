import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'

import { Picker } from './desktop-onboarding-overlay'

vi.mock('@/hermes', () => ({
  addCredentialPoolEntry: vi.fn(),
  cancelOAuthSession: vi.fn(),
  discoverProviderModels: vi.fn(),
  getGlobalModelOptions: vi.fn(async () => ({ providers: [] })),
  getProviderCatalog: vi.fn(async () => ({
    providers: [
      {
        auth_type: 'api_key',
        base_url: 'https://openrouter.ai/api/v1',
        credential_count: 0,
        key_env: 'OPENROUTER_API_KEY',
        name: 'OpenRouter',
        slug: 'openrouter',
        verified_model_count: 0
      },
      {
        auth_type: 'api_key',
        base_url: '',
        credential_count: 0,
        key_env: 'CUSTOM_API_KEY',
        name: 'OpenAI 兼容 / 中转站 / 本地',
        slug: 'custom',
        verified_model_count: 0
      }
    ]
  })),
  getRecommendedDefaultModel: vi.fn(),
  listOAuthProviders: vi.fn(),
  pollOAuthSession: vi.fn(),
  setEnvVar: vi.fn(),
  setModelAssignment: vi.fn(),
  startOAuthLogin: vi.fn(),
  submitOAuthCode: vi.fn(),
  validateModelRoute: vi.fn(),
  validateProviderCredential: vi.fn()
}))

function setApiOnlyState() {
  $desktopOnboarding.set({
    configured: false,
    flow: { status: 'idle' },
    mode: 'apikey',
    providers: [],
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false
  } satisfies DesktopOnboardingState)
}

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()

  try {
    window.localStorage.clear()
  } catch {
    // jsdom localStorage should always be present; ignore if not.
  }

  $desktopOnboarding.set({
    configured: null,
    flow: { status: 'idle' },
    mode: 'apikey',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false
  })
})

describe('onboarding Picker', () => {
  it('shows the provider API wizard instead of account sign-in choices', async () => {
    setApiOnlyState()
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('配置 LLM API')).toBeTruthy()
    expect(await screen.findByLabelText('提供商')).toBeTruthy()
    expect(screen.getByLabelText('API Key')).toBeTruthy()
    expect(screen.getByText('模型')).toBeTruthy()
    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(screen.queryByText('Anthropic API Key')).toBeNull()
    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })

  it('keeps the same API wizard in manual provider setup mode', async () => {
    setApiOnlyState()
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), manual: true })
    render(<Picker ctx={ctx} />)

    await waitFor(() => expect(screen.getByText('请选择提供商并输入 API Key，然后发现模型。')).toBeTruthy())
    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })
})
