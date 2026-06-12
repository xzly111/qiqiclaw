import type { ModelOptionProvider } from '@/types/hermes'

export const CUSTOM_PROVIDER_SLUG = 'custom'
export const CUSTOM_PROVIDER_LABEL = 'OpenAI 兼容 / 中转站 / 本地'
export const CUSTOM_PROVIDER_LABEL_EN = 'OpenAI Compatible / Relay / Local'

export interface ProviderSelectOption {
  label: string
  value: string
}

export interface EndpointPreset {
  baseUrl: string
  envKey?: string
  group: 'local' | 'remote'
  id: string
  name: string
}

export const CREDENTIAL_POOL_PROVIDER_OPTIONS: readonly ProviderSelectOption[] = [
  { value: CUSTOM_PROVIDER_SLUG, label: CUSTOM_PROVIDER_LABEL },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'lmstudio', label: 'LM Studio' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'xiaomi', label: 'Xiaomi MiMo' },
  { value: 'tencent-tokenhub', label: 'Tencent TokenHub' },
  { value: 'nvidia', label: 'NVIDIA NIM' },
  { value: 'copilot', label: 'GitHub Copilot' },
  { value: 'huggingface', label: 'Hugging Face' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'xai', label: 'xAI' },
  { value: 'zai', label: 'Z.AI / GLM' },
  { value: 'kimi-coding', label: 'Kimi (Coding Plan)' },
  { value: 'kimi-coding-cn', label: 'Kimi / Moonshot (China)' },
  { value: 'stepfun', label: 'StepFun Step Plan' },
  { value: 'minimax', label: 'MiniMax' },
  { value: 'minimax-cn', label: 'MiniMax (China)' },
  { value: 'alibaba', label: 'Alibaba Cloud (DashScope)' },
  { value: 'ollama-cloud', label: 'Ollama Cloud' },
  { value: 'arcee', label: 'Arcee AI' },
  { value: 'gmi', label: 'GMI Cloud' },
  { value: 'kilocode', label: 'Kilo Code' },
  { value: 'opencode-zen', label: 'OpenCode Zen' },
  { value: 'opencode-go', label: 'OpenCode Go' },
  { value: 'azure-foundry', label: 'Azure Foundry' },
  { value: 'ai-gateway', label: 'Vercel AI Gateway' }
]

export const ENDPOINT_PRESETS: readonly EndpointPreset[] = [
  { id: 'lmstudio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1', group: 'local' },
  { id: 'atomicchat', name: 'Atomic Chat', baseUrl: 'http://localhost:1337/v1', group: 'local' },
  { id: 'ollama', name: 'Ollama', baseUrl: 'http://localhost:11434/v1', group: 'local' },
  { id: 'vllm', name: 'vLLM', baseUrl: 'http://localhost:8000/v1', group: 'local' },
  { id: 'llamacpp', name: 'llama.cpp', baseUrl: 'http://localhost:8080/v1', group: 'local' },
  { id: 'groq', name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1', group: 'remote', envKey: 'GROQ_API_KEY' },
  { id: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', group: 'remote', envKey: 'DEEPSEEK_API_KEY' },
  { id: 'together', name: 'Together AI', baseUrl: 'https://api.together.xyz/v1', group: 'remote', envKey: 'TOGETHER_API_KEY' },
  { id: 'fireworks', name: 'Fireworks AI', baseUrl: 'https://api.fireworks.ai/inference/v1', group: 'remote', envKey: 'FIREWORKS_API_KEY' },
  { id: 'cerebras', name: 'Cerebras', baseUrl: 'https://api.cerebras.ai/v1', group: 'remote', envKey: 'CEREBRAS_API_KEY' },
  { id: 'mistral', name: 'Mistral', baseUrl: 'https://api.mistral.ai/v1', group: 'remote', envKey: 'MISTRAL_API_KEY' }
]

export function detectProviderFromBaseUrl(rawUrl: string): string | null {
  const url = rawUrl.trim().toLowerCase()

  if (!url) {
    return null
  }

  if (/(^|\/\/)openrouter\.ai(\/|:|$)/.test(url)) return 'openrouter'
  if (/(^|\/\/)api\.anthropic\.com(\/|:|$)/.test(url)) return 'anthropic'
  if (/(^|\/\/)api\.openai\.com(\/|:|$)/.test(url)) return CUSTOM_PROVIDER_SLUG
  if (/(^|\/\/)generativelanguage\.googleapis\.com(\/|:|$)/.test(url)) return 'gemini'
  if (/(^|\/\/)api\.x\.ai(\/|:|$)/.test(url)) return 'xai'
  if (/nousresearch\.com/.test(url)) return 'nous'
  if (/dashscope(-intl)?\.aliyuncs\.com/.test(url)) return 'alibaba'
  if (/api\.minimax(i)?\.(chat|com|io)/.test(url)) return 'minimax'

  const host = extractHost(url)

  if (host && isPrivateOrLoopback(host)) {
    return CUSTOM_PROVIDER_SLUG
  }

  if (/:(11434|1234|1337|8000|8080)(\/|$)/.test(url)) {
    return CUSTOM_PROVIDER_SLUG
  }

  return null
}

export function providerLabel(value: string, providers: readonly ModelOptionProvider[] = []): string {
  if (value === CUSTOM_PROVIDER_SLUG) {
    return CUSTOM_PROVIDER_LABEL
  }

  return (
    providers.find(provider => provider.slug === value)?.name ??
    CREDENTIAL_POOL_PROVIDER_OPTIONS.find(provider => provider.value === value)?.label ??
    value
  )
}

export function withCustomProvider(providers: readonly ModelOptionProvider[]): ModelOptionProvider[] {
  const rows = [...providers]

  if (!rows.some(provider => provider.slug === CUSTOM_PROVIDER_SLUG)) {
    rows.push({
      name: CUSTOM_PROVIDER_LABEL,
      slug: CUSTOM_PROVIDER_SLUG,
      models: [],
      authenticated: true,
      auth_type: 'api_key',
      key_env: 'CUSTOM_API_KEY'
    })
  } else {
    const idx = rows.findIndex(provider => provider.slug === CUSTOM_PROVIDER_SLUG)
    rows[idx] = {
      ...rows[idx],
      name: rows[idx].name || CUSTOM_PROVIDER_LABEL,
      authenticated: rows[idx].authenticated ?? true,
      models: rows[idx].models ?? []
    }
  }

  return rows
}

export function customEnvKeyForBaseUrl(rawUrl: string): string {
  const url = rawUrl.trim()

  if (!url) return 'CUSTOM_API_KEY'
  if (/openrouter\.ai/i.test(url)) return 'OPENROUTER_API_KEY'
  if (/anthropic\.com/i.test(url)) return 'ANTHROPIC_API_KEY'
  if (/openai\.com/i.test(url)) return 'OPENAI_API_KEY'
  if (/xiaomimimo\.com/i.test(url)) return 'XIAOMI_API_KEY'
  if (/tokenhub\.tencentmaas\.com/i.test(url)) return 'TOKENHUB_API_KEY'
  if (/integrate\.api\.nvidia\.com/i.test(url)) return 'NVIDIA_API_KEY'
  if (/api\.githubcopilot\.com/i.test(url)) return 'COPILOT_GITHUB_TOKEN'
  if (/huggingface\.co/i.test(url)) return 'HF_TOKEN'
  if (/generativelanguage\.googleapis\.com/i.test(url)) return 'GOOGLE_API_KEY'
  if (/api\.groq\.com/i.test(url)) return 'GROQ_API_KEY'
  if (/api\.deepseek\.com/i.test(url)) return 'DEEPSEEK_API_KEY'
  if (/api\.x\.ai/i.test(url)) return 'XAI_API_KEY'
  if (/api\.z\.ai/i.test(url)) return 'GLM_API_KEY'
  if (/api\.moonshot\.ai/i.test(url)) return 'KIMI_API_KEY'
  if (/api\.moonshot\.cn/i.test(url)) return 'KIMI_CN_API_KEY'
  if (/api\.stepfun\.ai/i.test(url)) return 'STEPFUN_API_KEY'
  if (/api\.minimax\.io/i.test(url)) return 'MINIMAX_API_KEY'
  if (/api\.minimaxi\.com/i.test(url)) return 'MINIMAX_CN_API_KEY'
  if (/dashscope.*aliyuncs\.com/i.test(url)) return 'DASHSCOPE_API_KEY'
  if (/ollama\.com/i.test(url)) return 'OLLAMA_API_KEY'
  if (/api\.arcee\.ai/i.test(url)) return 'ARCEEAI_API_KEY'
  if (/api\.gmi-serving\.com/i.test(url)) return 'GMI_API_KEY'
  if (/api\.kilo\.ai/i.test(url)) return 'KILOCODE_API_KEY'
  if (/opencode\.ai\/zen\/go/i.test(url)) return 'OPENCODE_GO_API_KEY'
  if (/opencode\.ai\/zen/i.test(url)) return 'OPENCODE_ZEN_API_KEY'
  if (/ai-gateway\.vercel\.sh/i.test(url)) return 'AI_GATEWAY_API_KEY'
  if (/api\.together\.xyz/i.test(url)) return 'TOGETHER_API_KEY'
  if (/api\.fireworks\.ai/i.test(url)) return 'FIREWORKS_API_KEY'
  if (/api\.cerebras\.ai/i.test(url)) return 'CEREBRAS_API_KEY'
  if (/api\.mistral\.ai/i.test(url)) return 'MISTRAL_API_KEY'
  if (/api\.perplexity\.ai/i.test(url)) return 'PERPLEXITY_API_KEY'
  return 'CUSTOM_API_KEY'
}

function extractHost(url: string): string | null {
  const stripped = url.replace(/^https?:\/\//, '').split('/')[0]

  if (!stripped) {
    return null
  }

  return stripped.split(':')[0] || null
}

function isPrivateOrLoopback(host: string): boolean {
  if (host === 'localhost') return true
  if (host === '127.0.0.1' || host === '::1' || host === '[::1]') return true
  if (/^10\./.test(host)) return true
  if (/^192\.168\./.test(host)) return true

  const match = host.match(/^172\.(\d+)\./)

  if (match) {
    const second = Number.parseInt(match[1], 10)

    if (second >= 16 && second <= 31) {
      return true
    }
  }

  return /\.local$/.test(host)
}
