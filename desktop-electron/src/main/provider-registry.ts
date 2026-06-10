/**
 * Canonical inference base URLs for built-in providers — mirrors
 * hermes-agent's `PROVIDER_REGISTRY` defaults.
 *
 * Lives in its own module (not in `model-discovery.ts` or `config.ts`)
 * to avoid a circular import: `model-discovery` already depends on
 * `config` for `readEnv`, and `config` needs this lookup for
 * `setModelConfig` to write the right `base_url:` when the user picks a
 * built-in provider entry that doesn't carry a baseUrl of its own.
 *
 * Values mirror qiqiclaw_cli/auth.py::PROVIDER_REGISTRY. Providers with
 * non-HTTP transports keep their canonical scheme so config.yaml matches
 * the backend selector.
 */
export const PROVIDER_BASE_URLS: Record<string, string> = {
  nous: "https://inference-api.nousresearch.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  lmstudio: "http://127.0.0.1:1234/v1",
  anthropic: "https://api.anthropic.com",
  "openai-codex": "https://chatgpt.com/backend-api/codex",
  "codex-cli": "https://chatgpt.com/backend-api/codex",
  xiaomi: "https://api.xiaomimimo.com/v1",
  "tencent-tokenhub": "https://tokenhub.tencentmaas.com/v1",
  nvidia: "https://integrate.api.nvidia.com/v1",
  "qwen-oauth": "https://portal.qwen.ai/v1",
  copilot: "https://api.githubcopilot.com",
  "copilot-acp": "acp://copilot",
  huggingface: "https://router.huggingface.co/v1",
  gemini: "https://generativelanguage.googleapis.com/v1beta",
  "google-gemini-cli": "cloudcode-pa://google",
  deepseek: "https://api.deepseek.com/v1",
  xai: "https://api.x.ai/v1",
  zai: "https://api.z.ai/api/paas/v4",
  "kimi-coding": "https://api.moonshot.ai/v1",
  "kimi-coding-cn": "https://api.moonshot.cn/v1",
  stepfun: "https://api.stepfun.ai/step_plan/v1",
  minimax: "https://api.minimax.io/anthropic",
  "minimax-oauth": "https://api.minimax.io/anthropic",
  "minimax-cn": "https://api.minimaxi.com/anthropic",
  alibaba: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
  "ollama-cloud": "https://ollama.com/v1",
  arcee: "https://api.arcee.ai/api/v1",
  gmi: "https://api.gmi-serving.com/v1",
  kilocode: "https://api.kilo.ai/api/gateway",
  "opencode-zen": "https://opencode.ai/zen/v1",
  "opencode-go": "https://opencode.ai/zen/go/v1",
  bedrock: "https://bedrock-runtime.us-east-1.amazonaws.com",
  "ai-gateway": "https://ai-gateway.vercel.sh/v1",
};

/**
 * Look up the canonical inference base URL for a built-in provider id.
 * Returns null when the provider isn't in the registry (e.g. `custom`,
 * `auto`, or anything user-defined).
 */
export function canonicalProviderBaseUrl(provider: string): string | null {
  const direct = PROVIDER_BASE_URLS[provider.toLowerCase()];
  return direct ?? null;
}

export function canonicalBackendProviderId(provider: string): string {
  const clean = provider.trim().toLowerCase();
  if (clean === "codex-cli") return "openai-codex";
  return clean;
}
