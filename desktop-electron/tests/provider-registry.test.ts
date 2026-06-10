import { describe, it, expect } from "vitest";
import {
  PROVIDER_BASE_URLS,
  canonicalProviderBaseUrl,
} from "../src/main/provider-registry";

describe("provider-registry", () => {
  describe("canonicalProviderBaseUrl", () => {
    it("returns backend canonical URLs for built-in providers", () => {
      expect(canonicalProviderBaseUrl("deepseek")).toBe(
        "https://api.deepseek.com/v1",
      );
      expect(canonicalProviderBaseUrl("gemini")).toBe(
        "https://generativelanguage.googleapis.com/v1beta",
      );
      expect(canonicalProviderBaseUrl("alibaba")).toBe(
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
      );
      expect(canonicalProviderBaseUrl("lmstudio")).toBe(
        "http://127.0.0.1:1234/v1",
      );
      expect(canonicalProviderBaseUrl("openai-codex")).toBe(
        "https://chatgpt.com/backend-api/codex",
      );
    });

    it("returns the canonical URL for Anthropic and OpenRouter", () => {
      expect(canonicalProviderBaseUrl("anthropic")).toBe(
        "https://api.anthropic.com",
      );
      expect(canonicalProviderBaseUrl("openrouter")).toBe(
        "https://openrouter.ai/api/v1",
      );
    });

    it("is case-insensitive on the provider id", () => {
      expect(canonicalProviderBaseUrl("DeepSeek")).toBe(
        "https://api.deepseek.com/v1",
      );
      expect(canonicalProviderBaseUrl("GEMINI")).toBe(
        "https://generativelanguage.googleapis.com/v1beta",
      );
    });

    it("returns null for providers that don't have a canonical URL", () => {
      // `custom` and `auto` are intentionally not in the registry — the
      // user must supply their own baseUrl.
      expect(canonicalProviderBaseUrl("custom")).toBeNull();
      expect(canonicalProviderBaseUrl("auto")).toBeNull();
      // Unknown/user-defined provider ids.
      expect(canonicalProviderBaseUrl("my-private-llm")).toBeNull();
      expect(canonicalProviderBaseUrl("")).toBeNull();
    });

    it("the registry covers every backend canonical provider with a default base URL", () => {
      const requiredBuiltins = [
        "nous",
        "openrouter",
        "lmstudio",
        "anthropic",
        "openai-codex",
        "xiaomi",
        "tencent-tokenhub",
        "nvidia",
        "qwen-oauth",
        "copilot",
        "copilot-acp",
        "huggingface",
        "gemini",
        "google-gemini-cli",
        "deepseek",
        "xai",
        "zai",
        "kimi-coding",
        "kimi-coding-cn",
        "stepfun",
        "minimax",
        "minimax-oauth",
        "minimax-cn",
        "alibaba",
        "ollama-cloud",
        "arcee",
        "gmi",
        "kilocode",
        "opencode-zen",
        "opencode-go",
        "bedrock",
        "ai-gateway",
      ];
      for (const provider of requiredBuiltins) {
        expect(PROVIDER_BASE_URLS[provider]).toBeTruthy();
      }
    });
  });
});
