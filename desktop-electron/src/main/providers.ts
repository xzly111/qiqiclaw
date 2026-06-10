export const PROVIDERS_WITHOUT_API_KEYS = new Set([
  "custom",
  "lmstudio",
  "ollama",
  "vllm",
  "llamacpp",
  "nous",
  "openai-codex",
  "codex-cli",
  "qwen-oauth",
  "google-gemini-cli",
  "minimax-oauth",
  "copilot-acp",
  "bedrock",
]);

export function providerDoesNotNeedApiKey(provider: string): boolean {
  return PROVIDERS_WITHOUT_API_KEYS.has(provider);
}
