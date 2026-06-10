# OpenClaw Plugin Mechanism Snapshot

Session source: upstream `openclaw/openclaw` main queried on 2026-05-16. Latest stable release observed: `v2026.5.12`; latest tag observed: `v2026.5.16-beta.1`.

Use this reference when assessing OpenClaw-to-QiQi claw migration or deciding whether to port an OpenClaw plugin feature.

## Core model

OpenClaw has two extension shapes:

1. Native OpenClaw plugins
   - Node/TypeScript ESM packages.
   - Require `openclaw.plugin.json` in the plugin root.
   - Runtime entry is declared in `package.json#openclaw.extensions`.
   - Loaded in-process by the Gateway; not sandboxed.
   - Runtime calls `register(api)` via `definePluginEntry(...)` and registers providers, channels, tools, hooks, CLI commands, HTTP routes, services, etc.

2. Compatible bundles
   - Content/metadata packs mapped from Codex, Claude, and Cursor ecosystems.
   - Markers include `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`, or default Claude/Cursor layouts.
   - Safer than native plugins because current mapping is mostly content/metadata: skills, command roots as skills, supported OpenClaw hook packs, MCP config, selected Claude settings/LSP defaults.
   - Some upstream features are detect-only: Claude agents/hooks.json/outputStyles, Cursor agents/hooks/rules, Codex metadata beyond supported mappings.

## Control plane vs data plane

OpenClaw strongly separates:

- Manifest/control plane: `openclaw.plugin.json`
  - Static metadata read before executing plugin code.
  - Identity, config validation, UI hints, setup/onboarding descriptors, activation hints, ownership contracts, skill roots, provider/channel/tool ownership.
  - Used to validate config and narrow loading without importing runtime.

- Runtime/data plane: `register(api)`
  - Actual behavior registration.
  - Registers tools, providers, channels, hooks, routes, commands, services, CLI backends, etc.

This split is the most valuable migration pattern for QiQi claw: prefer a manifest-first metadata/content layer before any runtime-plugin execution.

## Load pipeline

Gateway startup roughly:

1. Discover candidate plugin roots.
2. Read native manifests or compatible bundle metadata.
3. Reject unsafe candidates before runtime execution.
4. Normalize plugin config: `plugins.enabled`, `plugins.allow`, `plugins.deny`, `plugins.entries`, `plugins.slots`, `plugins.load.paths`.
5. Decide enablement/blocked/selected state.
6. Load enabled native modules only when needed.
7. Call `register(api)` and collect into a central plugin registry.
8. Core surfaces consume the registry.

Safety gates block candidates when the entry escapes the plugin root, path ownership is suspicious, or non-bundled plugin paths are unsafe/world-writable.

## Registry and ownership

Plugins register into a central registry. Core reads the registry instead of reaching into plugin modules.

Registry tracks plugin records, tools, hooks, channels, providers, gateway RPC handlers, HTTP routes, CLI registrars, background services, and plugin-owned commands.

OpenClaw's mental model:

- plugin = ownership boundary for a company or feature
- capability = core contract multiple plugins can implement or consume

Examples: OpenAI plugin can own text, speech, realtime, media understanding, and image generation. DuckDuckGo can own only web search. Feature/channel plugins should consume generic capability contracts instead of vendor-specific code.

## Important manifest fields

Common top-level fields:

- `id` — canonical plugin id, used in `plugins.entries.<id>`.
- `configSchema` — JSON Schema for `plugins.entries.<id>.config`.
- `enabledByDefault`, `enabledByDefaultOnPlatforms` — bundled-plugin default enablement.
- `providers`, `channels`, `cliBackends` — cheap ownership metadata.
- `providerCatalogEntry`, `modelSupport`, `modelCatalog`, `modelPricing`, `modelIdNormalization`, `providerEndpoints`, `providerRequest` — provider control-plane metadata.
- `providerAuthChoices`, `setup`, `uiHints`, `channelConfigs` — onboarding/config/status UI metadata.
- `activation` — planner hints only; not a lifecycle hook.
- `contracts` — static capability ownership snapshot.
- `skills` — skill directories relative to plugin root.

Key `contracts` lists include `tools`, `speechProviders`, `realtimeTranscriptionProviders`, `realtimeVoiceProviders`, `memoryEmbeddingProviders`, `mediaUnderstandingProviders`, `imageGenerationProviders`, `videoGenerationProviders`, `webFetchProviders`, `webSearchProviders`, `migrationProviders`, and `gatewayMethodDispatch`.

Tools registered with `api.registerTool(...)` must appear in `contracts.tools`. Optional tools should also appear under `toolMetadata.<tool>.optional: true`, and users enable them with `tools.allow`.

## Activation planning

`activation` is metadata used to narrow loading. It is not a lifecycle hook and does not replace `register(api)`.

Planner triggers include command, provider, agent harness, channel, route, and capability. Fallback ownership signals include `providers`, `channels`, `commandAliases`, `setup.providers`, `contracts.tools`, and hooks.

## Plugin hooks

Plugins can register typed hooks with `api.on(name, handler, opts)`.

Notable hooks:

- Agent turn: `before_model_resolve`, `agent_turn_prepare`, `before_prompt_build`, `before_agent_run`, `before_agent_reply`, `before_agent_finalize`, `agent_end`, `heartbeat_prompt_contribution`.
- Conversation observation: `model_call_started`, `model_call_ended`, `llm_input`, `llm_output`.
- Tools: `before_tool_call`, `after_tool_call`, `tool_result_persist`, `before_message_write`.
- Messaging: `inbound_claim`, `message_received`, `message_sending`, `message_sent`, `before_dispatch`, `reply_dispatch`.
- Sessions/subagents/lifecycle: `session_start`, `session_end`, `before_compaction`, `after_compaction`, `subagent_*`, `gateway_start`, `gateway_stop`, `cron_changed`, `before_install`.

Hook handlers run by descending priority; timeouts can be plugin-authored or operator-configured. Decision hooks may block, rewrite, cancel, or require approval depending on hook type.

## CLI/backend plugins

CLI backend plugins expose a local AI CLI as an OpenClaw model provider prefix such as `acme-cli/model`.

Manifest declares `cliBackends` and optional setup descriptors. Runtime calls `api.registerCliBackend(...)` with command, args, parser mode, model/session flags, watchdogs, image support, serialization, and optional advanced callbacks.

This idea can inform QiQi claw integrations with external coding CLIs, but direct code is TypeScript/OpenClaw-specific.

## Install and dependency model

Install sources: ClawHub, npm, npm-pack, git, local path/link, marketplace bundles.

Common commands:

- `openclaw plugins list --json`
- `openclaw plugins search "calendar"`
- `openclaw plugins install clawhub:<package>`
- `openclaw plugins install npm:<package>`
- `openclaw plugins install git:github.com/owner/repo@ref`
- `openclaw plugins install --link ./my-plugin`
- `openclaw plugins inspect <plugin-id> --runtime --json`
- `openclaw plugins update --all`
- `openclaw plugins uninstall <plugin-id>`
- `openclaw gateway restart`

Dependency policy:

- Install/update commands handle package manager work.
- Gateway startup/reload never runs package installation or dependency repair steps.
- npm installs are under `~/.openclaw/npm`; git installs under `~/.openclaw/git`.
- Local plugins must bring their own dependencies.

## Migration guidance for QiQi claw

Do not directly copy OpenClaw's runtime plugin system into QiQi claw. It is Node/TypeScript/Gateway-specific and native plugins are in-process arbitrary code.

Safer staged approach:

1. Start with a QiQi claw manifest/content-pack layer: id, name, config schema, skills, tool declarations, required commands, env vars, capabilities, activation hints.
2. Map content bundles first: skills, commands-as-skills, MCP config, docs/templates.
3. Build a registry for existing QiQi claw skills/toolsets before allowing third-party runtime code.
4. If runtime plugins are later needed, make them explicit opt-in with allowlists, path ownership checks, no world-writable roots, diagnostics/doctor, failure isolation, and preferably process isolation rather than direct import.

Most reusable ideas: manifest-first discovery, `contracts` ownership, activation planning, runtime inspect/diagnostics, dependency work only at install/update time, and bundle/content-pack compatibility.
