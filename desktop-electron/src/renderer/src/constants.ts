// ── Shared Types ────────────────────────────────────────

export interface FieldDef {
  key: string;
  label: string;
  type: string;
  hint: string;
}

export interface SectionDef {
  title: string;
  items: FieldDef[];
}

// ── Providers ───────────────────────────────────────────

export const PROVIDERS = {
  // Ordered for the Providers / model-picker dropdown. Values mirror
  // qiqiclaw_cli/models.py::CANONICAL_PROVIDERS, with `auto` first and
  // `custom` last for user-provided OpenAI-compatible endpoints.
  options: [
    { value: "auto", label: "constants.autoDetect" },
    { value: "nous", label: "constants.nousName" },
    { value: "openrouter", label: "constants.openrouterName" },
    { value: "lmstudio", label: "LM Studio" },
    { value: "anthropic", label: "constants.anthropicName" },
    { value: "openai-codex", label: "OpenAI Codex / Codex CLI" },
    { value: "xiaomi", label: "Xiaomi MiMo" },
    { value: "tencent-tokenhub", label: "Tencent TokenHub" },
    { value: "nvidia", label: "NVIDIA NIM" },
    { value: "qwen-oauth", label: "Qwen (OAuth)" },
    { value: "copilot", label: "GitHub Copilot" },
    { value: "copilot-acp", label: "GitHub Copilot ACP" },
    { value: "huggingface", label: "Hugging Face" },
    { value: "gemini", label: "constants.googleName" },
    { value: "google-gemini-cli", label: "Gemini (CLI OAuth)" },
    { value: "deepseek", label: "DeepSeek" },
    { value: "xai", label: "constants.xaiName" },
    { value: "zai", label: "Z.AI / GLM" },
    { value: "kimi-coding", label: "Kimi (Coding Plan)" },
    { value: "kimi-coding-cn", label: "Kimi / Moonshot (China)" },
    { value: "stepfun", label: "StepFun Step Plan" },
    { value: "minimax", label: "MiniMax" },
    { value: "minimax-oauth", label: "MiniMax (OAuth)" },
    { value: "minimax-cn", label: "MiniMax (China)" },
    { value: "alibaba", label: "Alibaba Cloud (DashScope)" },
    { value: "ollama-cloud", label: "Ollama Cloud" },
    { value: "arcee", label: "Arcee AI" },
    { value: "gmi", label: "GMI Cloud" },
    { value: "kilocode", label: "Kilo Code" },
    { value: "opencode-zen", label: "OpenCode Zen" },
    { value: "opencode-go", label: "OpenCode Go" },
    { value: "bedrock", label: "AWS Bedrock" },
    { value: "azure-foundry", label: "Azure Foundry" },
    { value: "ai-gateway", label: "Vercel AI Gateway" },
    { value: "custom", label: "constants.customOpenAICompatibleName" },
  ],

  labels: {
    nous: "constants.nousName",
    openrouter: "constants.openrouterName",
    lmstudio: "LM Studio",
    anthropic: "constants.anthropicName",
    "openai-codex": "OpenAI Codex / Codex CLI",
    xiaomi: "Xiaomi MiMo",
    "tencent-tokenhub": "Tencent TokenHub",
    nvidia: "NVIDIA NIM",
    "qwen-oauth": "Qwen (OAuth)",
    copilot: "GitHub Copilot",
    "copilot-acp": "GitHub Copilot ACP",
    huggingface: "Hugging Face",
    gemini: "constants.googleName",
    "google-gemini-cli": "Gemini (CLI OAuth)",
    deepseek: "DeepSeek",
    xai: "constants.xaiName",
    zai: "Z.AI / GLM",
    "kimi-coding": "Kimi (Coding Plan)",
    "kimi-coding-cn": "Kimi / Moonshot (China)",
    stepfun: "StepFun Step Plan",
    minimax: "MiniMax",
    "minimax-oauth": "MiniMax (OAuth)",
    "minimax-cn": "MiniMax (China)",
    alibaba: "Alibaba Cloud (DashScope)",
    "ollama-cloud": "Ollama Cloud",
    arcee: "Arcee AI",
    gmi: "GMI Cloud",
    kilocode: "Kilo Code",
    "opencode-zen": "OpenCode Zen",
    "opencode-go": "OpenCode Go",
    bedrock: "AWS Bedrock",
    "azure-foundry": "Azure Foundry",
    "ai-gateway": "Vercel AI Gateway",
    custom: "OpenAI Compatible / Local",
  } as Record<string, string>,

  setup: [
    {
      id: "openrouter",
      name: "constants.openrouterName",
      desc: "constants.openrouterDesc",
      tag: "constants.openrouterTag",
      envKey: "OPENROUTER_API_KEY",
      url: "https://openrouter.ai/keys",
      placeholder: "sk-or-v1-...",
      configProvider: "openrouter",
      baseUrl: "https://openrouter.ai/api/v1",
      needsKey: true,
    },
    {
      id: "anthropic",
      name: "constants.anthropicName",
      desc: "constants.anthropicDesc",
      tag: "",
      envKey: "ANTHROPIC_API_KEY",
      url: "https://console.anthropic.com/settings/keys",
      placeholder: "sk-ant-...",
      configProvider: "anthropic",
      baseUrl: "",
      needsKey: true,
    },
    {
      id: "deepseek",
      name: "DeepSeek",
      desc: "constants.deepseekHint",
      tag: "",
      envKey: "DEEPSEEK_API_KEY",
      url: "https://platform.deepseek.com/api_keys",
      placeholder: "sk-...",
      configProvider: "deepseek",
      baseUrl: "https://api.deepseek.com/v1",
      needsKey: true,
    },
    {
      id: "openai-codex",
      name: "constants.openaiCodexName",
      desc: "constants.openaiCodexDesc",
      tag: "constants.openaiCodexTag",
      envKey: "",
      url: "",
      placeholder: "",
      configProvider: "openai-codex",
      baseUrl: "https://chatgpt.com/backend-api/codex",
      needsKey: false,
    },
    {
      id: "gemini",
      name: "constants.googleName",
      desc: "constants.googleDesc",
      tag: "",
      envKey: "GOOGLE_API_KEY",
      url: "https://aistudio.google.com/app/apikey",
      placeholder: "AIza...",
      configProvider: "gemini",
      baseUrl: "https://generativelanguage.googleapis.com/v1beta",
      needsKey: true,
    },
    {
      id: "xai",
      name: "constants.xaiName",
      desc: "constants.xaiDesc",
      tag: "",
      envKey: "XAI_API_KEY",
      url: "https://console.x.ai",
      placeholder: "xai-...",
      configProvider: "xai",
      baseUrl: "https://api.x.ai/v1",
      needsKey: true,
    },
    {
      id: "alibaba",
      name: "Alibaba Cloud (DashScope)",
      desc: "Alibaba Cloud / DashScope Coding",
      tag: "",
      envKey: "DASHSCOPE_API_KEY",
      url: "https://dashscope.console.aliyun.com/apiKey",
      placeholder: "sk-...",
      configProvider: "alibaba",
      baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
      needsKey: true,
    },
    {
      id: "lmstudio",
      name: "LM Studio",
      desc: "Local LM Studio server",
      tag: "constants.localTag",
      envKey: "",
      url: "",
      placeholder: "",
      configProvider: "lmstudio",
      baseUrl: "http://127.0.0.1:1234/v1",
      needsKey: false,
    },
    {
      id: "nous",
      name: "constants.nousName",
      desc: "constants.nousDesc",
      tag: "constants.nousTag",
      envKey: "",
      url: "",
      placeholder: "",
      configProvider: "nous",
      baseUrl: "https://inference-api.nousresearch.com/v1",
      needsKey: false,
    },
    {
      id: "custom",
      name: "constants.customOpenAICompatibleName",
      desc: "constants.customHint",
      tag: "constants.localTag",
      envKey: "",
      url: "",
      placeholder: "sk-...",
      configProvider: "custom",
      baseUrl: "http://localhost:1234/v1",
      needsKey: false,
    },
  ],
};

export const CREDENTIAL_POOL_PROVIDERS = [
  { value: "custom", label: "constants.customOpenAICompatibleName" },
  { value: "openrouter", label: "constants.openrouterName" },
  { value: "lmstudio", label: "LM Studio" },
  { value: "anthropic", label: "constants.anthropicName" },
  { value: "xiaomi", label: "Xiaomi MiMo" },
  { value: "tencent-tokenhub", label: "Tencent TokenHub" },
  { value: "nvidia", label: "NVIDIA NIM" },
  { value: "copilot", label: "GitHub Copilot" },
  { value: "huggingface", label: "Hugging Face" },
  { value: "gemini", label: "constants.googleName" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "xai", label: "constants.xaiName" },
  { value: "zai", label: "Z.AI / GLM" },
  { value: "kimi-coding", label: "Kimi (Coding Plan)" },
  { value: "kimi-coding-cn", label: "Kimi / Moonshot (China)" },
  { value: "stepfun", label: "StepFun Step Plan" },
  { value: "minimax", label: "MiniMax" },
  { value: "minimax-cn", label: "MiniMax (China)" },
  { value: "alibaba", label: "Alibaba Cloud (DashScope)" },
  { value: "ollama-cloud", label: "Ollama Cloud" },
  { value: "arcee", label: "Arcee AI" },
  { value: "gmi", label: "GMI Cloud" },
  { value: "kilocode", label: "Kilo Code" },
  { value: "opencode-zen", label: "OpenCode Zen" },
  { value: "opencode-go", label: "OpenCode Go" },
  { value: "azure-foundry", label: "Azure Foundry" },
  { value: "ai-gateway", label: "Vercel AI Gateway" },
];

export interface LocalPreset {
  id: string;
  name: string;
  baseUrl: string;
  group: "local" | "remote";
  envKey?: string;
}

export const LOCAL_PRESETS: LocalPreset[] = [
  {
    id: "lmstudio",
    name: "constants.lmstudio",
    baseUrl: "http://localhost:1234/v1",
    group: "local",
  },
  {
    id: "atomicchat",
    name: "constants.atomicchat",
    baseUrl: "http://localhost:1337/v1",
    group: "local",
  },
  {
    id: "ollama",
    name: "constants.ollama",
    baseUrl: "http://localhost:11434/v1",
    group: "local",
  },
  {
    id: "vllm",
    name: "constants.vllm",
    baseUrl: "http://localhost:8000/v1",
    group: "local",
  },
  {
    id: "llamacpp",
    name: "constants.llamacpp",
    baseUrl: "http://localhost:8080/v1",
    group: "local",
  },
  {
    id: "groq",
    name: "constants.groq",
    baseUrl: "https://api.groq.com/openai/v1",
    group: "remote",
    envKey: "GROQ_API_KEY",
  },
  {
    id: "deepseek",
    name: "constants.deepseek",
    baseUrl: "https://api.deepseek.com/v1",
    group: "remote",
    envKey: "DEEPSEEK_API_KEY",
  },
  {
    id: "together",
    name: "constants.together",
    baseUrl: "https://api.together.xyz/v1",
    group: "remote",
    envKey: "TOGETHER_API_KEY",
  },
  {
    id: "fireworks",
    name: "constants.fireworks",
    baseUrl: "https://api.fireworks.ai/inference/v1",
    group: "remote",
    envKey: "FIREWORKS_API_KEY",
  },
  {
    id: "cerebras",
    name: "constants.cerebras",
    baseUrl: "https://api.cerebras.ai/v1",
    group: "remote",
    envKey: "CEREBRAS_API_KEY",
  },
  {
    id: "mistral",
    name: "constants.mistral",
    baseUrl: "https://api.mistral.ai/v1",
    group: "remote",
    envKey: "MISTRAL_API_KEY",
  },
];

// ── Theme ───────────────────────────────────────────────

export const THEME_OPTIONS = [
  { value: "system" as const, label: "constants.themeSystem" },
  { value: "light" as const, label: "constants.themeLight" },
  { value: "dark" as const, label: "constants.themeDark" },
];

export const THEME_STORAGE_KEY = "hermes-theme";

// ── Settings API Key Sections ───────────────────────────

export const SETTINGS_SECTIONS: SectionDef[] = [
  {
    title: "constants.sectionLlmProviders",
    items: [
      {
        key: "OPENROUTER_API_KEY",
        label: "constants.openrouterApiKey",
        type: "password",
        hint: "constants.openrouterHint",
      },
      {
        key: "ANTHROPIC_API_KEY",
        label: "constants.anthropicApiKey",
        type: "password",
        hint: "constants.anthropicHint",
      },
      {
        key: "LM_API_KEY",
        label: "LM Studio API Key",
        type: "password",
        hint: "LM Studio local server key; leave empty unless your server requires one",
      },
      {
        key: "XIAOMI_API_KEY",
        label: "Xiaomi MiMo API Key",
        type: "password",
        hint: "Xiaomi MiMo direct API credential",
      },
      {
        key: "TOKENHUB_API_KEY",
        label: "Tencent TokenHub API Key",
        type: "password",
        hint: "Tencent TokenHub direct API credential",
      },
      {
        key: "NVIDIA_API_KEY",
        label: "constants.nvidiaApiKey",
        type: "password",
        hint: "constants.nvidiaHint",
      },
      {
        key: "COPILOT_GITHUB_TOKEN",
        label: "GitHub Copilot Token",
        type: "password",
        hint: "GitHub Copilot token used by the backend copilot provider",
      },
      {
        key: "HF_TOKEN",
        label: "constants.hfToken",
        type: "password",
        hint: "constants.hfHint",
      },
      {
        key: "GOOGLE_API_KEY",
        label: "constants.googleApiKey",
        type: "password",
        hint: "constants.googleHint",
      },
      {
        key: "DEEPSEEK_API_KEY",
        label: "constants.deepseekApiKey",
        type: "password",
        hint: "constants.deepseekHint",
      },
      {
        key: "XAI_API_KEY",
        label: "constants.xaiApiKey",
        type: "password",
        hint: "constants.xaiHint",
      },
      {
        key: "GLM_API_KEY",
        label: "constants.glmApiKey",
        type: "password",
        hint: "constants.glmHint",
      },
      {
        key: "KIMI_API_KEY",
        label: "constants.kimiApiKey",
        type: "password",
        hint: "constants.kimiHint",
      },
      {
        key: "KIMI_CN_API_KEY",
        label: "Kimi China API Key",
        type: "password",
        hint: "Kimi / Moonshot China direct API credential",
      },
      {
        key: "STEPFUN_API_KEY",
        label: "StepFun API Key",
        type: "password",
        hint: "StepFun Step Plan API credential",
      },
      {
        key: "MINIMAX_API_KEY",
        label: "constants.minimaxApiKey",
        type: "password",
        hint: "constants.minimaxHint",
      },
      {
        key: "MINIMAX_CN_API_KEY",
        label: "constants.minimaxCnApiKey",
        type: "password",
        hint: "constants.minimaxCnHint",
      },
      {
        key: "DASHSCOPE_API_KEY",
        label: "DashScope API Key",
        type: "password",
        hint: "Alibaba Cloud DashScope API credential",
      },
      {
        key: "OLLAMA_API_KEY",
        label: "Ollama Cloud API Key",
        type: "password",
        hint: "Ollama Cloud API credential",
      },
      {
        key: "ARCEEAI_API_KEY",
        label: "Arcee AI API Key",
        type: "password",
        hint: "Arcee AI direct API credential",
      },
      {
        key: "GMI_API_KEY",
        label: "GMI Cloud API Key",
        type: "password",
        hint: "GMI Cloud direct API credential",
      },
      {
        key: "KILOCODE_API_KEY",
        label: "Kilo Code API Key",
        type: "password",
        hint: "Kilo Code gateway API credential",
      },
      {
        key: "OPENCODE_ZEN_API_KEY",
        label: "constants.opencodeZenApiKey",
        type: "password",
        hint: "constants.opencodeZenHint",
      },
      {
        key: "OPENCODE_GO_API_KEY",
        label: "constants.opencodeGoApiKey",
        type: "password",
        hint: "constants.opencodeGoHint",
      },
      {
        key: "AZURE_FOUNDRY_API_KEY",
        label: "Azure Foundry API Key",
        type: "password",
        hint: "Azure Foundry API key; set AZURE_FOUNDRY_BASE_URL for your deployment endpoint",
      },
      {
        key: "AZURE_FOUNDRY_BASE_URL",
        label: "Azure Foundry Endpoint",
        type: "text",
        hint: "Required endpoint for Azure Foundry provider",
      },
      {
        key: "AI_GATEWAY_API_KEY",
        label: "Vercel AI Gateway API Key",
        type: "password",
        hint: "Vercel AI Gateway API credential",
      },
      {
        key: "CUSTOM_API_KEY",
        label: "constants.customApiKey",
        type: "password",
        hint: "constants.customHint",
      },
    ],
  },
  {
    title: "constants.sectionToolApiKeys",
    items: [
      {
        key: "EXA_API_KEY",
        label: "constants.exaApiKey",
        type: "password",
        hint: "constants.exaHint",
      },
      {
        key: "PARALLEL_API_KEY",
        label: "constants.parallelApiKey",
        type: "password",
        hint: "constants.parallelHint",
      },
      {
        key: "TAVILY_API_KEY",
        label: "constants.tavilyApiKey",
        type: "password",
        hint: "constants.tavilyHint",
      },
      {
        key: "FIRECRAWL_API_KEY",
        label: "constants.firecrawlApiKey",
        type: "password",
        hint: "constants.firecrawlHint",
      },
      {
        key: "FAL_KEY",
        label: "constants.falKey",
        type: "password",
        hint: "constants.falHint",
      },
      {
        key: "HONCHO_API_KEY",
        label: "constants.honchoApiKey",
        type: "password",
        hint: "constants.honchoHint",
      },
    ],
  },
  {
    title: "constants.sectionBrowserAutomation",
    items: [
      {
        key: "BROWSERBASE_API_KEY",
        label: "constants.browserbaseApiKey",
        type: "password",
        hint: "constants.browserbaseHint",
      },
      {
        key: "BROWSERBASE_PROJECT_ID",
        label: "constants.browserbaseProjectId",
        type: "text",
        hint: "constants.browserbaseProjectHint",
      },
    ],
  },
  {
    title: "constants.sectionVoiceStt",
    items: [
      {
        key: "VOICE_TOOLS_OPENAI_KEY",
        label: "constants.voiceOpenaiKey",
        type: "password",
        hint: "constants.voiceOpenaiHint",
      },
    ],
  },
  {
    title: "constants.sectionResearchTraining",
    items: [
      {
        key: "TINKER_API_KEY",
        label: "constants.tinkerApiKey",
        type: "password",
        hint: "constants.tinkerHint",
      },
      {
        key: "WANDB_API_KEY",
        label: "constants.wandbKey",
        type: "password",
        hint: "constants.wandbHint",
      },
    ],
  },
];

// ── Gateway Sections ────────────────────────────────────

export const GATEWAY_SECTIONS: SectionDef[] = [
  {
    title: "constants.gatewayMessagingPlatforms",
    items: [
      {
        key: "TELEGRAM_BOT_TOKEN",
        label: "constants.telegramBotToken",
        type: "password",
        hint: "constants.telegramBotHint",
      },
      {
        key: "TELEGRAM_ALLOWED_USERS",
        label: "constants.telegramAllowedUsers",
        type: "text",
        hint: "constants.telegramUsersHint",
      },
      {
        key: "DISCORD_BOT_TOKEN",
        label: "constants.discordBotToken",
        type: "password",
        hint: "constants.discordBotHint",
      },
      {
        key: "DISCORD_ALLOWED_CHANNELS",
        label: "constants.discordAllowedChannels",
        type: "text",
        hint: "constants.discordChannelsHint",
      },
      {
        key: "SLACK_BOT_TOKEN",
        label: "constants.slackBotToken",
        type: "password",
        hint: "constants.slackBotHint",
      },
      {
        key: "SLACK_APP_TOKEN",
        label: "constants.slackAppToken",
        type: "password",
        hint: "constants.slackAppHint",
      },
      {
        key: "WHATSAPP_API_URL",
        label: "constants.whatsappApiUrl",
        type: "text",
        hint: "constants.whatsappUrlHint",
      },
      {
        key: "WHATSAPP_API_TOKEN",
        label: "constants.whatsappApiToken",
        type: "password",
        hint: "constants.whatsappTokenHint",
      },
      {
        key: "SIGNAL_PHONE_NUMBER",
        label: "constants.signalPhoneNumber",
        type: "text",
        hint: "constants.signalPhoneHint",
      },
      {
        key: "MATRIX_HOMESERVER",
        label: "constants.matrixHomeserver",
        type: "text",
        hint: "constants.matrixHomeHint",
      },
      {
        key: "MATRIX_USER_ID",
        label: "constants.matrixUserId",
        type: "text",
        hint: "constants.matrixUserHint",
      },
      {
        key: "MATRIX_ACCESS_TOKEN",
        label: "constants.matrixAccessToken",
        type: "password",
        hint: "constants.matrixTokenHint",
      },
      {
        key: "MATTERMOST_URL",
        label: "constants.mattermostUrl",
        type: "text",
        hint: "constants.mattermostUrlHint",
      },
      {
        key: "MATTERMOST_TOKEN",
        label: "constants.mattermostToken",
        type: "password",
        hint: "constants.mattermostTokenHint",
      },
      {
        key: "EMAIL_IMAP_SERVER",
        label: "constants.emailImapServer",
        type: "text",
        hint: "constants.emailImapHint",
      },
      {
        key: "EMAIL_SMTP_SERVER",
        label: "constants.emailSmtpServer",
        type: "text",
        hint: "constants.emailSmtpHint",
      },
      {
        key: "EMAIL_ADDRESS",
        label: "constants.emailAddress",
        type: "text",
        hint: "constants.emailAddrHint",
      },
      {
        key: "EMAIL_PASSWORD",
        label: "constants.emailPassword",
        type: "password",
        hint: "constants.emailPassHint",
      },
      {
        key: "SMS_PROVIDER",
        label: "constants.smsProvider",
        type: "text",
        hint: "constants.smsProviderHint",
      },
      {
        key: "TWILIO_ACCOUNT_SID",
        label: "constants.twilioAccountSid",
        type: "text",
        hint: "constants.twilioSidHint",
      },
      {
        key: "TWILIO_AUTH_TOKEN",
        label: "constants.twilioAuthToken",
        type: "password",
        hint: "constants.twilioTokenHint",
      },
      {
        key: "TWILIO_PHONE_NUMBER",
        label: "constants.twilioPhoneNumber",
        type: "text",
        hint: "constants.twilioPhoneHint",
      },
      {
        key: "BLUEBUBBLES_URL",
        label: "constants.bluebubblesUrl",
        type: "text",
        hint: "constants.bluebubblesUrlHint",
      },
      {
        key: "BLUEBUBBLES_PASSWORD",
        label: "constants.bluebubblesPassword",
        type: "password",
        hint: "constants.bluebubblesPassHint",
      },
      {
        key: "DINGTALK_APP_KEY",
        label: "constants.dingtalkAppKey",
        type: "password",
        hint: "constants.dingtalkKeyHint",
      },
      {
        key: "DINGTALK_APP_SECRET",
        label: "constants.dingtalkAppSecret",
        type: "password",
        hint: "constants.dingtalkSecretHint",
      },
      {
        key: "FEISHU_APP_ID",
        label: "constants.feishuAppId",
        type: "text",
        hint: "constants.feishuIdHint",
      },
      {
        key: "FEISHU_APP_SECRET",
        label: "constants.feishuAppSecret",
        type: "password",
        hint: "constants.feishuSecretHint",
      },
      {
        key: "WECOM_CORP_ID",
        label: "constants.wecomCorpId",
        type: "text",
        hint: "constants.wecomCorpHint",
      },
      {
        key: "WECOM_AGENT_ID",
        label: "constants.wecomAgentId",
        type: "text",
        hint: "constants.wecomAgentHint",
      },
      {
        key: "WECOM_SECRET",
        label: "constants.wecomSecret",
        type: "password",
        hint: "constants.wecomSecretHint",
      },
      {
        key: "WEIXIN_BOT_TOKEN",
        label: "constants.weixinBotToken",
        type: "password",
        hint: "constants.weixinTokenHint",
      },
      {
        key: "WEBHOOK_SECRET",
        label: "constants.webhookSecret",
        type: "password",
        hint: "constants.webhookHint",
      },
      {
        key: "HASS_URL",
        label: "constants.haUrl",
        type: "text",
        hint: "constants.haUrlHint",
      },
      {
        key: "HASS_TOKEN",
        label: "constants.haToken",
        type: "password",
        hint: "constants.haTokenHint",
      },
    ],
  },
];

export interface PlatformDef {
  key: string;
  label: string;
  description: string;
  fields: string[]; // env keys that belong to this platform
}

export const GATEWAY_PLATFORMS: PlatformDef[] = [
  {
    key: "telegram",
    label: "constants.platformTelegram",
    description: "constants.platformTelegramDesc",
    fields: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS"],
  },
  {
    key: "discord",
    label: "constants.platformDiscord",
    description: "constants.platformDiscordDesc",
    fields: ["DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_CHANNELS"],
  },
  {
    key: "slack",
    label: "constants.platformSlack",
    description: "constants.platformSlackDesc",
    fields: ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
  },
  {
    key: "whatsapp",
    label: "constants.platformWhatsapp",
    description: "constants.platformWhatsappDesc",
    fields: ["WHATSAPP_API_URL", "WHATSAPP_API_TOKEN"],
  },
  {
    key: "signal",
    label: "constants.platformSignal",
    description: "constants.platformSignalDesc",
    fields: ["SIGNAL_PHONE_NUMBER"],
  },
  {
    key: "matrix",
    label: "constants.platformMatrix",
    description: "constants.platformMatrixDesc",
    fields: ["MATRIX_HOMESERVER", "MATRIX_USER_ID", "MATRIX_ACCESS_TOKEN"],
  },
  {
    key: "mattermost",
    label: "constants.platformMattermost",
    description: "constants.platformMattermostDesc",
    fields: ["MATTERMOST_URL", "MATTERMOST_TOKEN"],
  },
  {
    key: "email",
    label: "constants.platformEmail",
    description: "constants.platformEmailDesc",
    fields: [
      "EMAIL_IMAP_SERVER",
      "EMAIL_SMTP_SERVER",
      "EMAIL_ADDRESS",
      "EMAIL_PASSWORD",
    ],
  },
  {
    key: "sms",
    label: "constants.platformSms",
    description: "constants.platformSmsDesc",
    fields: [
      "SMS_PROVIDER",
      "TWILIO_ACCOUNT_SID",
      "TWILIO_AUTH_TOKEN",
      "TWILIO_PHONE_NUMBER",
    ],
  },
  {
    key: "bluebubbles",
    label: "constants.platformImessage",
    description: "constants.platformImessageDesc",
    fields: ["BLUEBUBBLES_URL", "BLUEBUBBLES_PASSWORD"],
  },
  {
    key: "dingtalk",
    label: "constants.platformDingtalk",
    description: "constants.platformDingtalkDesc",
    fields: ["DINGTALK_APP_KEY", "DINGTALK_APP_SECRET"],
  },
  {
    key: "feishu",
    label: "constants.platformFeishu",
    description: "constants.platformFeishuDesc",
    fields: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
  },
  {
    key: "wecom",
    label: "constants.platformWecom",
    description: "constants.platformWecomDesc",
    fields: ["WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_SECRET"],
  },
  {
    key: "weixin",
    label: "constants.platformWeixin",
    description: "constants.platformWeixinDesc",
    fields: ["WEIXIN_BOT_TOKEN"],
  },
  {
    key: "webhooks",
    label: "constants.platformWebhooks",
    description: "constants.platformWebhooksDesc",
    fields: ["WEBHOOK_SECRET"],
  },
  {
    key: "home_assistant",
    label: "constants.platformHomeAssistant",
    description: "constants.platformHomeAssistantDesc",
    fields: ["HASS_URL", "HASS_TOKEN"],
  },
];

// ── Install ─────────────────────────────────────────────

export const UNIX_INSTALL_CMD =
  "curl -fsSL https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.sh | bash";
export const INSTALL_CMD_UNIX = UNIX_INSTALL_CMD;
export const WINDOWS_INSTALL_CMD =
  "powershell -NoProfile -ExecutionPolicy Bypass -c \"$qiqiclawHome = Join-Path $env:USERPROFILE '.qiqiclaw'; $installDir = Join-Path $qiqiclawHome 'qiqiclaw'; $installer = [ScriptBlock]::Create((irm https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.ps1 -UseBasicParsing)); & $installer -SkipSetup -QiqiclawHome $qiqiclawHome -InstallDir $installDir\"";
export const INSTALL_CMD =
  typeof window !== "undefined" &&
  window.electron?.process?.platform === "win32"
    ? WINDOWS_INSTALL_CMD
    : UNIX_INSTALL_CMD;

export const INSTALL_CMD_WIN = WINDOWS_INSTALL_CMD;

export function getInstallCmd(): string {
  return window.electron?.process?.platform === "win32"
    ? WINDOWS_INSTALL_CMD
    : UNIX_INSTALL_CMD;
}

// Helper to resolve i18n key or return as-is
export function tk(t: (key: string) => string, value: string): string {
  if (value.startsWith("constants.")) {
    return t(value);
  }
  return value;
}
