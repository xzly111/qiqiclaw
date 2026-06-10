import { describe, expect, it } from "vitest";
import { expectedEnvKeyForModel } from "../src/main/installer";

// Regression tests for #236: the install gate's .env check hard-coded
// only OPENROUTER_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY, so users
// configured for DeepSeek, Groq, Mistral, etc. saw the "set AI provider"
// first-run screen every restart, even with a valid key in .env.
//
// The new `expectedEnvKeyForModel(provider, baseUrl)` returns the
// canonical env var name the gateway expects per provider, falling back
// to URL-pattern matching for `custom` / `auto` providers pointed at a
// known endpoint, then to null for unrecognized configurations (where
// the caller does a permissive `*_API_KEY` scan).

describe("expectedEnvKeyForModel — provider-name lookup", () => {
  it.each([
    ["openrouter", "OPENROUTER_API_KEY"],
    ["anthropic", "ANTHROPIC_API_KEY"],
    ["lmstudio", "LM_API_KEY"],
    ["gemini", "GOOGLE_API_KEY"],
    ["xai", "XAI_API_KEY"],
    ["deepseek", "DEEPSEEK_API_KEY"], // the specific provider from issue #236
    ["huggingface", "HF_TOKEN"], // exception to the *_API_KEY convention
    ["alibaba", "DASHSCOPE_API_KEY"],
    ["minimax", "MINIMAX_API_KEY"],
    ["glm", "GLM_API_KEY"],
    ["kimi", "KIMI_API_KEY"],
    ["stepfun", "STEPFUN_API_KEY"],
    ["ai-gateway", "AI_GATEWAY_API_KEY"],
  ])("maps provider %s → %s", (provider, expected) => {
    expect(expectedEnvKeyForModel(provider, "")).toBe(expected);
  });

  it("is case-insensitive on the provider name", () => {
    expect(expectedEnvKeyForModel("DeepSeek", "")).toBe("DEEPSEEK_API_KEY");
    expect(expectedEnvKeyForModel("ANTHROPIC", "")).toBe("ANTHROPIC_API_KEY");
  });

  it("trims surrounding whitespace from the provider name", () => {
    expect(expectedEnvKeyForModel("  gemini  ", "")).toBe("GOOGLE_API_KEY");
  });
});

describe("expectedEnvKeyForModel — URL fallback for custom/auto providers", () => {
  it("recognizes a known endpoint when provider is 'custom'", () => {
    expect(
      expectedEnvKeyForModel("custom", "https://api.deepseek.com/v1"),
    ).toBe("DEEPSEEK_API_KEY");
    expect(
      expectedEnvKeyForModel("custom", "https://api.groq.com/openai/v1"),
    ).toBe("GROQ_API_KEY");
    expect(
      expectedEnvKeyForModel("custom", "https://openrouter.ai/api/v1"),
    ).toBe("OPENROUTER_API_KEY");
  });

  it("recognizes a known endpoint when provider is 'auto'", () => {
    expect(expectedEnvKeyForModel("auto", "https://api.mistral.ai/v1")).toBe(
      "MISTRAL_API_KEY",
    );
    expect(
      expectedEnvKeyForModel(
        "auto",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
      ),
    ).toBe("DASHSCOPE_API_KEY");
  });

  it("returns null for unknown provider with unknown URL", () => {
    expect(
      expectedEnvKeyForModel("custom", "http://localhost:1234/v1"),
    ).toBeNull();
    expect(expectedEnvKeyForModel("custom", "")).toBeNull();
    expect(expectedEnvKeyForModel("", "")).toBeNull();
  });

  it("provider-name match wins over URL fallback when both are present", () => {
    // If somehow provider is "anthropic" but baseUrl points at deepseek
    // (configuration error), trust the provider name — the gateway
    // dispatches by provider, not URL.
    expect(
      expectedEnvKeyForModel("anthropic", "https://api.deepseek.com/v1"),
    ).toBe("ANTHROPIC_API_KEY");
  });
});

// The actual install-gate behavior is exercised end-to-end by importing
// the module under a per-test QIQICLAW_HOME — same pattern as the other
// installer tests. Skipped here because the existing tests already cover
// the file-existence half; the gap was provider awareness, which the
// pure-function tests above pin down.
