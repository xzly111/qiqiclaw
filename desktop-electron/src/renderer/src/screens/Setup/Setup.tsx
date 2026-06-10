import { useState } from "react";
import { ArrowRight, ExternalLink } from "../../assets/icons";
import { PROVIDERS, LOCAL_PRESETS } from "../../constants";
import { useI18n } from "../../components/useI18n";
import VerifyWarningBanner from "../../components/VerifyWarningBanner";
import BrandLogo from "../../components/common/BrandLogo";

interface SetupProps {
  onComplete: () => void;
  verifyWarning?: boolean;
  onReinstall?: () => void;
  onDismissVerifyWarning?: () => void;
}

function Setup({
  onComplete,
  verifyWarning,
  onReinstall,
  onDismissVerifyWarning,
}: SetupProps): React.JSX.Element {
  const { t } = useI18n();
  const [selectedProvider, setSelectedProvider] = useState("openrouter");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://localhost:1234/v1");
  const [modelName, setModelName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [showKey, setShowKey] = useState(false);

  const provider = PROVIDERS.setup.find((p) => p.id === selectedProvider)!;
  const isLocal = selectedProvider === "custom";

  function applyLocalPreset(presetBaseUrl: string): void {
    setBaseUrl(presetBaseUrl);
  }

  function resolveCustomEnvKey(url: string): string {
    const preset = LOCAL_PRESETS.find((p) => p.baseUrl === url);
    if (preset?.envKey) return preset.envKey;
    if (/openrouter\.ai/i.test(url)) return "OPENROUTER_API_KEY";
    if (/anthropic\.com/i.test(url)) return "ANTHROPIC_API_KEY";
    if (/openai\.com/i.test(url)) return "OPENAI_API_KEY";
    if (/xiaomimimo\.com/i.test(url)) return "XIAOMI_API_KEY";
    if (/tokenhub\.tencentmaas\.com/i.test(url)) return "TOKENHUB_API_KEY";
    if (/integrate\.api\.nvidia\.com/i.test(url)) return "NVIDIA_API_KEY";
    if (/api\.githubcopilot\.com/i.test(url)) return "COPILOT_GITHUB_TOKEN";
    if (/huggingface\.co/i.test(url)) return "HF_TOKEN";
    if (/generativelanguage\.googleapis\.com/i.test(url))
      return "GOOGLE_API_KEY";
    if (/api\.groq\.com/i.test(url)) return "GROQ_API_KEY";
    if (/api\.deepseek\.com/i.test(url)) return "DEEPSEEK_API_KEY";
    if (/api\.x\.ai/i.test(url)) return "XAI_API_KEY";
    if (/api\.z\.ai/i.test(url)) return "GLM_API_KEY";
    if (/api\.moonshot\.ai/i.test(url)) return "KIMI_API_KEY";
    if (/api\.moonshot\.cn/i.test(url)) return "KIMI_CN_API_KEY";
    if (/api\.stepfun\.ai/i.test(url)) return "STEPFUN_API_KEY";
    if (/api\.minimax\.io/i.test(url)) return "MINIMAX_API_KEY";
    if (/api\.minimaxi\.com/i.test(url)) return "MINIMAX_CN_API_KEY";
    if (/dashscope.*aliyuncs\.com/i.test(url)) return "DASHSCOPE_API_KEY";
    if (/ollama\.com/i.test(url)) return "OLLAMA_API_KEY";
    if (/api\.arcee\.ai/i.test(url)) return "ARCEEAI_API_KEY";
    if (/api\.gmi-serving\.com/i.test(url)) return "GMI_API_KEY";
    if (/api\.kilo\.ai/i.test(url)) return "KILOCODE_API_KEY";
    if (/opencode\.ai\/zen\/go/i.test(url)) return "OPENCODE_GO_API_KEY";
    if (/opencode\.ai\/zen/i.test(url)) return "OPENCODE_ZEN_API_KEY";
    if (/ai-gateway\.vercel\.sh/i.test(url)) return "AI_GATEWAY_API_KEY";
    if (/api\.together\.xyz/i.test(url)) return "TOGETHER_API_KEY";
    if (/api\.fireworks\.ai/i.test(url)) return "FIREWORKS_API_KEY";
    if (/api\.cerebras\.ai/i.test(url)) return "CEREBRAS_API_KEY";
    if (/api\.mistral\.ai/i.test(url)) return "MISTRAL_API_KEY";
    if (/api\.perplexity\.ai/i.test(url)) return "PERPLEXITY_API_KEY";
    return "CUSTOM_API_KEY";
  }

  function labelForCustomEndpoint(url: string): string {
    try {
      const parsed = new URL(url.trim());
      return parsed.hostname || "OpenAI Compatible Endpoint";
    } catch {
      return "OpenAI Compatible Endpoint";
    }
  }

  async function handleContinue(): Promise<void> {
    if (provider.needsKey && !apiKey.trim()) {
      setError(t("setup.missingApiKey"));
      return;
    }
    if (isLocal && !baseUrl.trim()) {
      setError(t("setup.missingServerUrl"));
      return;
    }

    setSaving(true);
    setError("");

    try {
      if (provider.needsKey && provider.envKey) {
        await window.qiqiclawAPI.setEnv(provider.envKey, apiKey.trim());
      } else if (isLocal && apiKey.trim()) {
        const envKey = resolveCustomEnvKey(baseUrl.trim());
        await window.qiqiclawAPI.setEnv(envKey, apiKey.trim());
        await window.qiqiclawAPI.addCredentialPoolEntry(
          "custom",
          apiKey.trim(),
          labelForCustomEndpoint(baseUrl),
          baseUrl.trim(),
        );
      }

      const configProvider = isLocal ? "custom" : provider.configProvider;
      const configBaseUrl = isLocal ? baseUrl.trim() : provider.baseUrl;
      const configModel = modelName.trim() || "";
      await window.qiqiclawAPI.setModelConfig(
        configProvider,
        configModel,
        configBaseUrl,
      );

      const ready = await window.qiqiclawAPI.prepareLocalApi();
      if (!ready.ok) {
        setError(ready.error || t("setup.saveFailed"));
        setSaving(false);
        return;
      }

      onComplete();
    } catch {
      setError(t("setup.saveFailed"));
      setSaving(false);
    }
  }

  return (
    <div className="screen setup-screen">
      {verifyWarning && onReinstall && onDismissVerifyWarning && (
        <VerifyWarningBanner
          onReinstall={onReinstall}
          onDismiss={onDismissVerifyWarning}
        />
      )}
      <h1 className="setup-title">{t("setup.title")}</h1>
      <p className="setup-subtitle">{t("setup.subtitle")}</p>

      <div className="setup-provider-grid">
        {PROVIDERS.setup.map((p) => (
          <button
            key={p.id}
            className={`setup-provider-card ${selectedProvider === p.id ? "selected" : ""}`}
            onClick={() => {
              setSelectedProvider(p.id);
              setError("");
            }}
          >
            <BrandLogo provider={p.id} size={24} matchTheme={true} />
            <div className="setup-provider-name">{t(p.name)}</div>
            {p.tag && <div className="setup-provider-tag">{t(p.tag)}</div>}
          </button>
        ))}
      </div>

      <div className="setup-form">
        {isLocal ? (
          <>
            <label className="setup-label">{t("setup.localGroupLabel")}</label>
            <div className="setup-local-presets">
              {LOCAL_PRESETS.filter((p) => p.group === "local").map(
                (preset) => (
                  <button
                    key={preset.id}
                    className={`setup-local-preset ${baseUrl === preset.baseUrl ? "active" : ""}`}
                    onClick={() => applyLocalPreset(preset.baseUrl)}
                  >
                    {t(`setup.localPresets.${preset.id}`)}
                  </button>
                ),
              )}
            </div>

            <label className="setup-label" style={{ marginTop: 12 }}>
              {t("setup.remoteGroupLabel")}
            </label>
            <div className="setup-local-presets">
              {LOCAL_PRESETS.filter((p) => p.group === "remote").map(
                (preset) => (
                  <button
                    key={preset.id}
                    className={`setup-local-preset ${baseUrl === preset.baseUrl ? "active" : ""}`}
                    onClick={() => applyLocalPreset(preset.baseUrl)}
                  >
                    {t(`setup.localPresets.${preset.id}`)}
                  </button>
                ),
              )}
            </div>

            <label className="setup-label" style={{ marginTop: 16 }}>
              {t("setup.serverUrl")}
            </label>
            <input
              className="input"
              type="text"
              placeholder={t("setup.modelBaseUrlPlaceholder")}
              value={baseUrl}
              onChange={(e) => {
                setBaseUrl(e.target.value);
                setError("");
              }}
              autoFocus
            />
            <div className="setup-field-hint">
              {t("setup.customServerHint")}
            </div>

            <label className="setup-label" style={{ marginTop: 16 }}>
              {t("setup.customApiKeyLabel")}{" "}
              <span className="setup-label-optional">
                {t("common.optional")}
              </span>
            </label>
            <div className="setup-input-group">
              <input
                className="input"
                type={showKey ? "text" : "password"}
                placeholder="sk-..."
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setError("");
                }}
              />
              <button
                className="setup-toggle-visibility"
                onClick={() => setShowKey(!showKey)}
                type="button"
              >
                {showKey ? t("common.hide") : t("common.show")}
              </button>
            </div>
            <div className="setup-field-hint">
              {t("setup.customApiKeyHint")}
            </div>

            <label className="setup-label" style={{ marginTop: 16 }}>
              {t("setup.modelName")}{" "}
              <span className="setup-label-optional">
                {t("common.optional")}
              </span>
            </label>
            <input
              className="input"
              type="text"
              placeholder={t("setup.modelNamePlaceholder")}
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
            />
            <div className="setup-field-hint">
              {t("setup.defaultModelHint")}
            </div>
          </>
        ) : provider.needsKey ? (
          <>
            <label className="setup-label">
              {t("setup.apiKeyLabel", { provider: t(provider.name) })}
            </label>
            <div className="setup-input-group">
              <input
                className="input"
                type={showKey ? "text" : "password"}
                placeholder={provider.placeholder}
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setError("");
                }}
                onKeyDown={(e) => e.key === "Enter" && handleContinue()}
                autoFocus
              />
              <button
                className="setup-toggle-visibility"
                onClick={() => setShowKey(!showKey)}
                type="button"
              >
                {showKey ? t("common.hide") : t("common.show")}
              </button>
            </div>

            <button
              className="setup-link"
              onClick={() => window.qiqiclawAPI.openExternal(provider.url)}
            >
              {t("setup.noKeyHint")}
              <ExternalLink size={12} />
            </button>
          </>
        ) : (
          <>
            <div className="setup-field-hint">
              {t("setup.noApiKeyRequired", { provider: t(provider.name) })}
            </div>

            <label className="setup-label" style={{ marginTop: 16 }}>
              {t("setup.modelName")}{" "}
              <span className="setup-label-optional">
                {t("common.optional")}
              </span>
            </label>
            <input
              className="input"
              type="text"
              placeholder={t("setup.modelNamePlaceholder")}
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleContinue()}
              autoFocus
            />
            <div className="setup-field-hint">
              {t("setup.defaultModelHint")}
            </div>
          </>
        )}

        {error && <div className="setup-error">{error}</div>}

        <button
          className="btn btn-primary setup-continue"
          onClick={handleContinue}
          disabled={
            saving ||
            (provider.needsKey && !apiKey.trim()) ||
            (isLocal && !baseUrl.trim())
          }
          style={{ marginTop: isLocal ? 20 : 0 }}
        >
          {saving ? t("setup.saving") : t("setup.continue")}
          {!saving && <ArrowRight size={16} />}
        </button>
      </div>
    </div>
  );
}

export default Setup;
