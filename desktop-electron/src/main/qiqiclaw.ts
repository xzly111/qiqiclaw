import { ChildProcess, execFile, spawn } from "child_process";
import { randomUUID } from "crypto";
import {
  existsSync,
  readFileSync,
  appendFileSync,
  unlinkSync,
  mkdirSync,
  openSync,
  closeSync,
  statSync,
} from "fs";
import { join } from "path";
import http from "http";
import https from "https";
import {
  QIQICLAW_HOME,
  QIQICLAW_REPO,
  QIQICLAW_PYTHON,
  buildQiqiclawEnv,
  qiqiclawCliArgs,
} from "./installer";
import {
  getApiServerKey,
  getConnectionConfig,
  getCredentialPool,
  getModelConfig,
  readEnv,
} from "./config";
import {
  getSshTunnelUrl,
  isSshTunnelActive,
  isSshTunnelHealthy,
  startSshTunnel,
} from "./ssh-tunnel";
import {
  pidIsAliveAs,
  stripAnsi,
  profileHome,
  getActiveProfileNameSync,
} from "./utils";
import { readModels } from "./models";
import { HIDDEN_SUBPROCESS_OPTIONS } from "./process-options";
import { type Attachment, escapeXmlAttr } from "../shared/attachments";

const LOCAL_API_URL = "http://127.0.0.1:8642";
const QIQICLAW_DASHBOARD_PORT = "9119";
const QIQICLAW_GATEWAY_PORT = "8642";
const QIQICLAW_GATEWAY_HOST = "127.0.0.1";
const API_READY_TIMEOUT_MS = 60000;
const API_HEALTH_REQUEST_TIMEOUT_MS = 3000;
const GATEWAY_STDERR_LOG = join(QIQICLAW_HOME, "gateway-stderr.log");

/**
 * Normalise a remote-mode URL the user typed into the connection
 * settings.  Strips trailing slashes and, importantly, a trailing
 * `/v1` segment — callers append `/v1/<path>` themselves, so leaving
 * the user's `/v1` would produce `http://host/v1/v1/chat/completions`
 * → 404.  Reported as #266 (multiple users entered the URL "with
 * /v1" because the gateway's curl examples show that form).
 *
 * Also tolerates trailing whitespace and the rare `/v1/` (slash-suffixed)
 * form.  Returns the cleaned string.
 */
export function normaliseRemoteUrl(raw: string): string {
  let url = (raw || "").trim();
  // Strip trailing slashes
  url = url.replace(/\/+$/, "");
  // Strip trailing `/v1` (callers append /v1/<path> themselves)
  url = url.replace(/\/v1$/i, "");
  return url;
}

export function coerceQiqiclawGatewayUrl(raw: string): string {
  const url = normaliseRemoteUrl(raw);
  if (!url) return url;

  try {
    const parsed = new URL(url);
    const hasOnlyRootPath = !parsed.pathname || parsed.pathname === "/";
    if (parsed.port === QIQICLAW_DASHBOARD_PORT && hasOnlyRootPath) {
      parsed.port = QIQICLAW_GATEWAY_PORT;
      parsed.pathname = "";
      parsed.search = "";
      parsed.hash = "";
      return parsed.toString().replace(/\/$/, "");
    }
  } catch {
    // Keep the original normalised string; the request path will surface the error.
  }

  return url;
}

export function getApiUrl(): string {
  const conn = getConnectionConfig();
  if (conn.mode === "ssh") {
    const sshUrl = getSshTunnelUrl();
    if (!sshUrl) throw new Error("SSH tunnel is not active");
    return normaliseRemoteUrl(sshUrl);
  }
  if (conn.mode === "remote" && conn.remoteUrl) {
    return coerceQiqiclawGatewayUrl(conn.remoteUrl);
  }
  return LOCAL_API_URL;
}

export function isRemoteMode(): boolean {
  const mode = getConnectionConfig().mode;
  return mode === "remote" || mode === "ssh";
}

/** True only for pure remote HTTP — SSH tunnel has full local access via SSH exec */
export function isRemoteOnlyMode(): boolean {
  return getConnectionConfig().mode === "remote";
}

// Cached API key read from the remote .env when SSH tunnel starts
let _sshRemoteApiKey = "";

export function setSshRemoteApiKey(key: string): void {
  _sshRemoteApiKey = key;
}

export function getRemoteAuthHeader(): Record<string, string> {
  const conn = getConnectionConfig();
  if (conn.mode === "ssh") {
    if (_sshRemoteApiKey)
      return { Authorization: `Bearer ${_sshRemoteApiKey}` };
    return {};
  }
  if (conn.mode === "remote" && conn.apiKey) {
    return { Authorization: `Bearer ${conn.apiKey}` };
  }
  return {};
}

function resolveRemoteApiKey(url: string, apiKey?: string): string {
  if (apiKey !== undefined) return apiKey;

  const conn = getConnectionConfig();
  if (conn.mode !== "remote" || !conn.apiKey || !conn.remoteUrl) return "";
  if (normaliseRemoteUrl(conn.remoteUrl) !== normaliseRemoteUrl(url)) {
    return "";
  }
  return conn.apiKey;
}

export async function ensureSshTunnelIfNeeded(): Promise<void> {
  const conn = getConnectionConfig();
  if (
    conn.mode === "ssh" &&
    (!isSshTunnelActive() || !(await isSshTunnelHealthy()))
  ) {
    await startSshTunnel(conn.ssh);
  }
}

/**
 * Providers whose chat path the desktop wires up explicitly with
 * `OPENAI_BASE_URL` + a resolved `OPENAI_API_KEY` — rather than relying
 * on the agent's native provider routing.
 *
 * The original set was just *local* LLM endpoints (lmstudio / ollama /
 * vllm / llamacpp) plus the generic `custom` entry. That meant the
 * built-in remote OpenAI-compatible providers (Groq, DeepSeek,
 * Together, Fireworks, Cerebras, Mistral) — which are defined in
 * `src/renderer/src/constants.ts:LOCAL_PRESETS` with their own
 * `baseUrl` + `envKey` — slipped past this branch and tripped an
 * upstream fallback that misroutes the request to
 * OpenAI's API while still sending the user's provider key, producing
 * a 401 like *"Incorrect API key provided: sk-… You can find your
 * API key at https://platform.openai.com/account/api-keys."*
 *
 * Including them here lets the same `URL_KEY_MAP` lookup that already
 * handles `provider="custom"` with a known commercial host also fire
 * when the user picks the built-in entry — same routing, same key,
 * no upstream-fallback leak.
 */
const OPENAI_COMPAT_PROVIDERS = new Set([
  // Generic
  "custom",
  // Local LLMs
  "lmstudio",
  "ollama",
  "vllm",
  "llamacpp",
]);

function isOpenAICompatProvider(provider: string): boolean {
  const lower = (provider || "").trim().toLowerCase();
  return OPENAI_COMPAT_PROVIDERS.has(lower) || lower.startsWith("custom:");
}

// Map base-URL patterns to the API key env var they need
const URL_KEY_MAP: Array<{ pattern: RegExp; envKey: string }> = [
  { pattern: /openrouter\.ai/i, envKey: "OPENROUTER_API_KEY" },
  { pattern: /anthropic\.com/i, envKey: "ANTHROPIC_API_KEY" },
  { pattern: /openai\.com/i, envKey: "OPENAI_API_KEY" },
  { pattern: /huggingface\.co/i, envKey: "HF_TOKEN" },
  { pattern: /api\.groq\.com/i, envKey: "GROQ_API_KEY" },
  { pattern: /api\.deepseek\.com/i, envKey: "DEEPSEEK_API_KEY" },
  { pattern: /api\.together\.xyz/i, envKey: "TOGETHER_API_KEY" },
  { pattern: /api\.fireworks\.ai/i, envKey: "FIREWORKS_API_KEY" },
  { pattern: /api\.cerebras\.ai/i, envKey: "CEREBRAS_API_KEY" },
  { pattern: /api\.mistral\.ai/i, envKey: "MISTRAL_API_KEY" },
  { pattern: /api\.perplexity\.ai/i, envKey: "PERPLEXITY_API_KEY" },
];

export function normaliseOpenAICompatBaseUrl(raw: string): string {
  const trimmed = (raw || "").trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  try {
    const parsed = new URL(trimmed);
    const path = parsed.pathname.replace(/\/+$/, "");
    if (!path || path === "/") {
      parsed.pathname = "/v1";
      parsed.search = "";
      parsed.hash = "";
      return parsed.toString().replace(/\/+$/, "");
    }
  } catch {
    // Keep the original value; the downstream request will surface invalid URLs.
  }
  return trimmed;
}

function baseUrlAliases(raw: string): string[] {
  const trimmed = (raw || "").trim().replace(/\/+$/, "");
  const normalised = normaliseOpenAICompatBaseUrl(trimmed);
  const aliases = [trimmed, normalised];
  try {
    const parsed = new URL(normalised || trimmed);
    const path = parsed.pathname.replace(/\/+$/, "");
    if (path.toLowerCase() === "/v1") {
      parsed.pathname = "/";
      parsed.search = "";
      parsed.hash = "";
      aliases.push(parsed.toString().replace(/\/+$/, ""));
    }
  } catch {
    /* best effort */
  }
  return Array.from(new Set(aliases.filter(Boolean)));
}

function credentialPoolKeyForBaseUrl(
  baseUrl: string,
  profile: string | undefined,
): string {
  const aliases = new Set(baseUrlAliases(baseUrl).map((x) => x.toLowerCase()));
  if (aliases.size === 0) return "";
  const pool = getCredentialPool(profile);
  for (const entries of Object.values(pool)) {
    for (const entry of entries || []) {
      const entryAliases = baseUrlAliases(entry.base_url || "").map((x) =>
        x.toLowerCase(),
      );
      if (!entryAliases.some((x) => aliases.has(x))) continue;
      const token =
        (entry.access_token || "").trim() ||
        (entry.api_key || "").trim() ||
        (entry.key || "").trim();
      if (token) return token;
    }
  }
  return "";
}

interface ChatHandle {
  abort: () => void;
}

// ────────────────────────────────────────────────────
//  API Server health check
// ────────────────────────────────────────────────────

function isApiServerReady(): Promise<boolean> {
  return new Promise((resolve) => {
    const url = `${getApiUrl()}/health`;
    const mod = url.startsWith("https") ? https : http;
    const req = mod.request(
      url,
      {
        method: "GET",
        timeout: API_HEALTH_REQUEST_TIMEOUT_MS,
        headers: getRemoteAuthHeader(),
      },
      (res) => {
        resolve(res.statusCode === 200);
        res.resume();
      },
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForApiServerReady(
  timeoutMs = API_READY_TIMEOUT_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  let delayMs = 250;
  while (Date.now() < deadline) {
    if (await isApiServerReady()) return true;
    await delay(delayMs);
    delayMs = Math.min(Math.round(delayMs * 1.4), 1500);
  }
  return false;
}

function readTail(path: string, maxChars = 4000): string {
  try {
    if (!existsSync(path)) return "";
    const size = statSync(path).size;
    const content = readFileSync(path, "utf-8");
    return content.slice(Math.max(0, content.length - Math.min(size, maxChars)));
  } catch {
    return "";
  }
}

function gatewayNotReadyError(): string {
  const pid = readPidFile();
  const stderrTail = readTail(GATEWAY_STDERR_LOG, 1800).trim();
  const parts = [
    "QiQiClaw gateway did not become ready on 127.0.0.1:8642.",
    `PID file: ${pid ?? "none"}`,
    `Python: ${QIQICLAW_PYTHON} (${existsSync(QIQICLAW_PYTHON) ? "exists" : "missing"})`,
    `Repo: ${QIQICLAW_REPO} (${existsSync(QIQICLAW_REPO) ? "exists" : "missing"})`,
    `stderr log: ${GATEWAY_STDERR_LOG}`,
  ];
  if (stderrTail) {
    parts.push(`Recent stderr:\n${stripAnsi(stderrTail).slice(-1800)}`);
  }
  return parts.join("\n");
}

function isConnectionRefusedError(err: Error & { code?: string }): boolean {
  return (
    err.code === "ECONNREFUSED" ||
    /(?:^|\s)ECONNREFUSED(?:\s|$)/i.test(err.message)
  );
}

function localBackendMissingError(): string | null {
  const missing: string[] = [];
  if (!existsSync(QIQICLAW_REPO)) {
    missing.push(`Repo missing: ${QIQICLAW_REPO}`);
  }
  if (!existsSync(QIQICLAW_PYTHON)) {
    missing.push(`Python missing: ${QIQICLAW_PYTHON}`);
  }
  if (missing.length === 0) return null;

  return [
    "QiQiClaw local backend is not installed or is incomplete.",
    "请先在桌面端运行安装/更新后端；如果刚清理过 .qiqiclaw，请重新执行安装流程。",
    `Home: ${QIQICLAW_HOME}`,
    ...missing,
    "Expected gateway endpoint after install: http://127.0.0.1:8642",
  ].join("\n");
}

// ────────────────────────────────────────────────────
//  Ensure API server is enabled in config
// ────────────────────────────────────────────────────

function ensureApiServerConfig(): void {
  try {
    const configPath = join(QIQICLAW_HOME, "config.yaml");
    if (!existsSync(configPath)) return;
    const content = readFileSync(configPath, "utf-8");
    // If api_server is already configured, skip
    if (/api_server/i.test(content)) return;
    const addition = `
# Desktop app API server (auto-configured)
platforms:
  api_server:
    enabled: true
    extra:
      port: 8642
      host: "127.0.0.1"
`;
    appendFileSync(configPath, addition, "utf-8");
  } catch {
    /* non-fatal */
  }
}

// ────────────────────────────────────────────────────
//  HTTP API streaming (fast path — no process spawn)
// ────────────────────────────────────────────────────

/**
 * Pull the streaming reasoning / thinking text from one SSE `delta`
 * object, if present. Two shapes seen in the wild:
 *
 *   - DeepSeek (reasoning models): `delta.reasoning_content`
 *   - OpenAI o1/o3-style streams + some OpenRouter routes:
 *     `delta.reasoning` (older OpenAI thinking-mode docs also use this
 *     field name).
 *
 * Returns `""` (falsy) for any other shape, so the caller can skip
 * forwarding without a null check.
 *
 * Exported so we can unit-test the field-extraction without booting
 * the whole HTTP path. (#352)
 */
export function extractReasoningDelta(delta: unknown): string {
  if (!delta || typeof delta !== "object") return "";
  const d = delta as Record<string, unknown>;
  if (typeof d.reasoning_content === "string" && d.reasoning_content)
    return d.reasoning_content;
  if (typeof d.reasoning === "string" && d.reasoning) return d.reasoning;
  return "";
}

export interface ChatCallbacks {
  onChunk: (text: string) => void;
  /** Streaming reasoning / thinking tokens, when the provider emits them
   *  alongside `content`. DeepSeek surfaces these as `delta.reasoning_content`;
   *  OpenAI o1/o3-style streams use `delta.reasoning`. Forwarded on a
   *  dedicated channel so the renderer can render the thinking bubble
   *  live instead of waiting for a state-DB refresh on focus change
   *  (issue #352). */
  onReasoningChunk?: (text: string) => void;
  onDone: (sessionId?: string) => void;
  onError: (error: string) => void;
  onToolProgress?: (tool: string) => void;
  onUsage?: (usage: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    cost?: number;
    rateLimitRemaining?: number;
    rateLimitReset?: number;
  }) => void;
}

type ChatContent =
  | string
  | Array<
      | { type: "text"; text: string }
      | { type: "image_url"; image_url: { url: string } }
    >;

/**
 * Build the OpenAI-compatible `content` payload for a user turn.
 *
 * - No attachments → plain string (preserves prompt-cache friendliness for
 *   the all-text path).
 * - Text-file attachments → inlined into the text part as `<file …>…</file>`
 *   wrappers (the gateway rejects `file`/`input_file` content parts, see
 *   gateway/platforms/api_server.py:263).
 * - Image attachments → emitted as `image_url` parts in the OpenAI vision
 *   format, which the gateway accepts and converts for Anthropic providers.
 * - Path-ref attachments → appended as `[Attached file: <abs-path>]` lines
 *   so the agent's existing file-reading skills can pick them up.  Works
 *   for PDFs/docx/binaries the gateway won't pass through inline.
 */
export function buildUserContent(
  text: string,
  attachments?: Attachment[],
): ChatContent {
  if (!attachments || attachments.length === 0) return text;

  const textFiles = attachments.filter((a) => a.kind === "text-file");
  const pathRefs = attachments.filter(
    (a) => a.kind === "path-ref" && typeof a.path === "string" && a.path,
  );
  const images = attachments.filter(
    (a) => a.kind === "image" && typeof a.dataUrl === "string" && a.dataUrl,
  );

  const parts: string[] = [];
  if (text.trim()) parts.push(text);
  for (const f of textFiles) {
    if (typeof f.text !== "string") continue;
    const name = escapeXmlAttr(f.name);
    const mime = escapeXmlAttr(f.mime || "text/plain");
    parts.push(`<file name="${name}" mime="${mime}">\n${f.text}\n</file>`);
  }
  if (pathRefs.length > 0) {
    const lines = pathRefs.map((f) => `[Attached file: ${f.path}]`);
    parts.push(lines.join("\n"));
  }
  const composedText = parts.join("\n\n");

  if (images.length === 0) return composedText;

  const imageParts = images.map((img) => ({
    type: "image_url" as const,
    image_url: { url: img.dataUrl! },
  }));

  // Omit the text part entirely when there's nothing to say — some
  // providers (Anthropic via Bedrock, certain vision endpoints) reject an
  // empty-string text part as `invalid_content_part`.
  if (!composedText) return imageParts;

  return [{ type: "text" as const, text: composedText }, ...imageParts];
}

/**
 * Build the system message that scopes a conversation to a working folder
 * (issue #27). Returns null when no folder is set (undefined / empty /
 * whitespace) so callers can skip injection. Exported for unit testing.
 */
export function contextFolderSystemMessage(
  contextFolder?: string,
): { role: "system"; content: string } | null {
  const folder = contextFolder?.trim();
  if (!folder) return null;
  return {
    role: "system",
    content:
      `The working folder for this conversation is ${folder}. ` +
      `When the user asks you to read, create, modify, or run project ` +
      `files, use the file, terminal, and code-execution tools with ` +
      `absolute paths under this folder.`,
  };
}

function sendMessageViaApi(
  message: string,
  cb: ChatCallbacks,
  profile?: string,
  _resumeSessionId?: string,
  history?: Array<{ role: string; content: string }>,
  attachments?: Attachment[],
  contextFolder?: string,
): ChatHandle {
  const mc = getModelConfig(profile);
  const controller = new AbortController();

  // Build full conversation from history + current message (standard OpenAI format).
  // History items are kept text-only — attachments from prior turns live in
  // the gateway's session state when resuming via session_id.
  const messages: Array<{ role: string; content: ChatContent }> = [];
  if (history && history.length > 0) {
    for (const msg of history) {
      messages.push({
        role: msg.role === "agent" ? "assistant" : msg.role,
        content: msg.content,
      });
    }
  }
  if (history && history.length > 0) {
    const provider = mc.provider || "auto";
    const model = mc.model || "deepseek-chat";
    messages.unshift({
      role: "system",
      content:
        `The active model for this request is ${model} from provider ${provider}. ` +
        "Use this active model context for all following turns, and ignore any earlier conversation text that described a previous active model or provider.",
    });
  }
  const userContent = buildUserContent(message, attachments);
  messages.push({ role: "user", content: userContent });

  // Context folder (issue #27): when the conversation is bound to a working
  // folder, prepend a system message so the agent scopes file/terminal work
  // there. Injected only at the request-build step — the renderer's visible
  // transcript stays clean, and getSessionMessages filters non-user/assistant
  // roles, so reloaded sessions stay clean too.
  const ctxSystem = contextFolderSystemMessage(contextFolder);
  if (ctxSystem) messages.unshift(ctxSystem);

  const body = JSON.stringify({
    model: mc.model || "deepseek-chat",
    messages,
    stream: true,
    ...(_resumeSessionId ? { session_id: _resumeSessionId } : {}),
  });

  // Encode the body up-front into a Buffer so we can:
  //  1. Set `Content-Length` accurately based on byte length (NOT char
  //     count — JSON.stringify of an image data URL is ASCII so they
  //     match, but multi-byte chars in user text would diverge).
  //  2. Disable Node's default `Transfer-Encoding: chunked` framing for
  //     bodies written via `req.write(body); req.end();`. Chunked
  //     framing skips the gateway's `body_limit_middleware` (which
  //     inspects Content-Length only), so an oversized payload that
  //     should produce a clean 413 "body_too_large" gets the
  //     misleading 400 "Invalid JSON in request body" via aiohttp's
  //     client_max_size overflow path. See #405.
  const bodyBuf = Buffer.from(body, "utf-8");

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Content-Length": String(bodyBuf.length),
    ...getRemoteAuthHeader(),
  };
  // Local API server key (API_SERVER_KEY in the profile's .env /
  // config.yaml) only applies in local mode — in remote/SSH mode the
  // remote endpoint's own auth header (set above) is authoritative and
  // must not be overwritten.
  if (!isRemoteMode()) {
    const apiServerKey = getApiServerKey(profile);
    console.log(
      "[hermes] apiServerKey=",
      apiServerKey ? apiServerKey.slice(0, 12) + "…" : "(none)",
    );
    if (apiServerKey) {
      headers.Authorization = `Bearer ${apiServerKey}`;
    }
  }

  // Session id: send both the QiQiClaw and legacy QiQiClaw headers so the
  // desktop can talk to either compatible gateway without forking sessions.
  // The gateway otherwise falls back to its `_derive_chat_session_id` fingerprint —
  // sha256(system_prompt + first_user_message)[:16] — which collides
  // across every chat whose first user message is the same (e.g. "Hi").
  // The collision silently fragments state.db rows across unrelated
  // conversations and, post-#352, surfaces as old-session content
  // bleeding into new chats when our end-of-stream merge reads
  // getSessionMessages(). Filed upstream as
  // NousResearch/hermes-agent#7484 (security framing — same root cause).
  //
  // Format: `desk-<ms>-<uuidv4>`. UUIDv4 alone is collision-safe
  // probabilistically (~10⁻³⁶ for any pair); the timestamp prefix makes
  // it defensively unique even under a hypothetical PRNG bug, and the
  // `desk-` tag makes desktop-originated sessions visually distinct
  // from the gateway's fingerprint-derived `api-<hash>` ids in
  // state.db / logs.
  //
  // Gate on auth: the gateway rejects explicit session headers with 403
  // when API_SERVER_KEY isn't configured (its history-load is gated
  // behind auth). The desktop auto-generates API_SERVER_KEY at install
  // and remote mode supplies its own bearer, so in practice this
  // branch is always taken; the guard exists only so a misconfigured
  // local install degrades to the pre-fix (fingerprint) behaviour
  // rather than 403-looping.
  const hasAuth = "Authorization" in headers;
  let sessionId =
    _resumeSessionId || (hasAuth ? `desk-${Date.now()}-${randomUUID()}` : "");
  if (sessionId) {
    headers["X-QiQi-Claw-Session-Id"] = sessionId;
    headers["X-QiQiClaw-Session-Id"] = sessionId;
  }
  let hasContent = false;
  let finished = false; // guard against double callbacks
  let lastError = ""; // capture embedded error messages
  // Tool progress pattern: `emoji tool_name` or `emoji description`
  const toolProgressRe = /^`([^\s`]+)\s+([^`]+)`$/;

  function finish(error?: string): void {
    if (finished) return;
    finished = true;
    console.log(
      "[hermes] finish called:",
      error ? `error=${error}` : "done",
      "sessionId=",
      sessionId,
    );
    if (error) {
      cb.onError(error);
    } else {
      cb.onDone(sessionId || undefined);
    }
  }

  function probeRealError(): void {
    // When streaming returns empty, make a non-streaming request to surface the real error
    const probeBody = JSON.stringify({
      model: mc.model || "deepseek-chat",
      messages: [{ role: "user", content: userContent }],
      stream: false,
    });
    const probeBodyBuf = Buffer.from(probeBody, "utf-8");
    // Per-request Content-Length (the outer `headers` object's value
    // belongs to the streaming request — reusing it here would lie about
    // this body's size and break the framing the same way the missing
    // Content-Length did before #405). Spread + override.
    const probeHeaders = {
      ...headers,
      "Content-Length": String(probeBodyBuf.length),
    };
    const probeUrl = `${getApiUrl()}/v1/chat/completions`;
    const probeMod = probeUrl.startsWith("https") ? https : http;
    const probeReq = probeMod.request(
      probeUrl,
      { method: "POST", headers: probeHeaders },
      (res) => {
        let raw = "";
        res.on("data", (d) => {
          raw += d.toString();
        });
        res.on("end", () => {
          try {
            const parsed = JSON.parse(raw);
            const content = parsed.choices?.[0]?.message?.content || "";
            const errMsg = parsed.error?.message || "";
            finish(
              content ||
                errMsg ||
                "No response received from the model. Check your model configuration and API key.",
            );
          } catch {
            finish(
              "No response received from the model. Check your model configuration and API key.",
            );
          }
        });
      },
    );
    probeReq.on("error", () => {
      finish(
        "No response received from the model. Check your model configuration and API key.",
      );
    });
    probeReq.write(probeBodyBuf);
    probeReq.end();
  }

  /** Handle a custom SSE event (non-data lines with `event:` prefix). */
  function processCustomEvent(eventType: string, data: string): void {
    if (eventType === "hermes.tool.progress" && cb.onToolProgress) {
      try {
        const payload = JSON.parse(data);
        const label = payload.label || payload.tool || "";
        const emoji = payload.emoji || "";
        cb.onToolProgress(emoji ? `${emoji} ${label}` : label);
      } catch {
        /* malformed — skip */
      }
    }
  }

  function processSseData(data: string): boolean {
    if (data === "[DONE]") {
      if (hasContent) {
        finish();
      } else if (lastError) {
        finish(lastError);
      } else {
        // Streaming returned empty — probe non-streaming to get the real error
        probeRealError();
      }
      return true; // signals done
    }
    try {
      const parsed = JSON.parse(data);

      // Capture error responses forwarded through SSE
      if (parsed.error) {
        lastError = parsed.error.message || JSON.stringify(parsed.error);
        return false;
      }

      const choice = parsed.choices?.[0];
      const delta = choice?.delta;

      // Extract usage from final chunk (with optional cost + rate limit info)
      if (parsed.usage && cb.onUsage) {
        cb.onUsage({
          promptTokens: parsed.usage.prompt_tokens || 0,
          completionTokens: parsed.usage.completion_tokens || 0,
          totalTokens: parsed.usage.total_tokens || 0,
          cost: parsed.usage.cost,
          rateLimitRemaining: parsed.usage.rate_limit_remaining,
          rateLimitReset: parsed.usage.rate_limit_reset,
        });
      }

      // Reasoning / thinking tokens, when the provider emits them.
      // Forwarded on a dedicated callback so the renderer can render the
      // thinking bubble live (#352). We do NOT set `hasContent = true`
      // here — reasoning alone shouldn't suppress the "empty stream"
      // diagnostic probe.
      const reasoningDelta = extractReasoningDelta(delta);
      if (reasoningDelta && cb.onReasoningChunk) {
        cb.onReasoningChunk(reasoningDelta);
      }

      if (delta?.content) {
        const content = delta.content.trim();
        // Legacy: Detect tool progress lines injected into content: `🔍 search_web`
        const match = toolProgressRe.exec(content);
        if (match && cb.onToolProgress) {
          cb.onToolProgress(`${match[1]} ${match[2]}`);
        } else {
          hasContent = true;
          cb.onChunk(delta.content);
        }
      }
    } catch {
      /* malformed chunk — skip */
    }
    return false;
  }

  const chatUrl = `${getApiUrl()}/v1/chat/completions`;
  const requester = chatUrl.startsWith("https") ? https.request : http.request;
  const req = requester(
    chatUrl,
    {
      method: "POST",
      headers,
      signal: controller.signal,
      timeout: 120000,
    },
    (res) => {
      const sid =
        res.headers["x-qiqi-claw-session-id"] ||
        res.headers["x-hermes-session-id"];
      if (sid && typeof sid === "string") sessionId = sid;

      if (res.statusCode !== 200) {
        let errBody = "";
        res.on("data", (d) => {
          errBody += d.toString();
        });
        res.on("end", () => {
          try {
            const err = JSON.parse(errBody);
            const parsedMessage =
              err.error?.message || err.detail || err.message || "";
            if (
              res.statusCode === 403 &&
              /Session continuation requires API key authentication/i.test(
                parsedMessage,
              )
            ) {
              finish(
                "QiQiClaw gateway 未配置 API_SERVER_KEY，但桌面端正在发送会话续接头。请在远程连接设置中清空 API key，或在 QiQiClaw gateway 配置 API_SERVER_KEY 后重启 gateway。",
              );
              return;
            }
            if (
              res.statusCode === 405 &&
              /\/v1\/chat\/completions$/.test(chatUrl)
            ) {
              finish(
                "API error 405: 当前聊天接口指向的服务不接受 POST /v1/chat/completions。请使用 QiQiClaw gateway 端口 8642，不要使用 dashboard 端口 9119。",
              );
              return;
            }
            finish(parsedMessage || `API error ${res.statusCode}`);
          } catch {
            if (res.statusCode === 405) {
              finish(
                "API error 405: 当前聊天接口指向的服务不接受 POST /v1/chat/completions。请使用 QiQiClaw gateway 端口 8642，不要使用 dashboard 端口 9119。",
              );
              return;
            }
            finish(
              `API server returned ${res.statusCode}: ${errBody.slice(0, 200)}`,
            );
          }
        });
        return;
      }

      let buffer = "";

      /** Parse an SSE block which may contain `event:` and `data:` lines. */
      function processSseBlock(block: string): boolean {
        let eventType = "";
        let dataLine = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            dataLine = line.slice(6);
          }
        }
        if (!dataLine) return false;
        if (eventType) {
          // Custom event (e.g. hermes.tool.progress) — never signals [DONE]
          processCustomEvent(eventType, dataLine);
          return false;
        }
        return processSseData(dataLine);
      }

      res.on("data", (chunk: Buffer) => {
        buffer += chunk.toString();
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (processSseBlock(part)) return;
        }
      });

      res.on("end", () => {
        if (buffer.trim()) {
          for (const part of buffer.split("\n\n")) {
            if (processSseBlock(part)) return;
          }
        }
        // Signal completion — even when no content was received
        if (!hasContent && !lastError) {
          probeRealError();
          return;
        }
        finish(hasContent ? undefined : lastError);
      });

      res.on("error", (err) => {
        if (err.message === "aborted" || err.name === "AbortError") return;
        finish(`Stream error: ${err.message}`);
      });
    },
  );

  req.on("error", (err) => {
    if (err.name === "AbortError") return;
    finish(`API request failed: ${err.message}`);
  });
  req.on("timeout", () => {
    req.destroy();
    finish(
      "API request timed out. Check the SSH tunnel and remote QiQiClaw gateway.",
    );
  });

  req.write(bodyBuf);
  req.end();

  return {
    abort: () => {
      controller.abort();
    },
  };
}

// ────────────────────────────────────────────────────
//  CLI fallback (slow path — spawns process)
// ────────────────────────────────────────────────────

const NOISE_PATTERNS = [/^[╭╰│╮╯─┌┐└┘┤├┬┴┼]/, /⚕\s*QiQiClaw/];

function sendMessageViaCli(
  message: string,
  cb: ChatCallbacks,
  profile?: string,
  resumeSessionId?: string,
  attachments?: Attachment[],
): ChatHandle {
  // CLI fallback can't pipe multimodal content; inline text-file attachments
  // and ignore images.  The gateway is the supported attachment path; this
  // is only hit when the API server isn't reachable.
  if (attachments && attachments.length > 0) {
    const textFiles = attachments.filter(
      (a) => a.kind === "text-file" && typeof a.text === "string",
    );
    if (textFiles.length > 0) {
      const wrapped = textFiles
        .map(
          (f) =>
            `<file name="${escapeXmlAttr(f.name)}" mime="${escapeXmlAttr(f.mime || "text/plain")}">\n${f.text}\n</file>`,
        )
        .join("\n\n");
      message = message.trim() ? `${message}\n\n${wrapped}` : wrapped;
    }
  }
  const mc = getModelConfig(profile);
  const profileEnv = readEnv(profile);

  const args = qiqiclawCliArgs();
  if (profile && profile !== "default") {
    args.push("-p", profile);
  }
  args.push("chat", "-q", message, "-Q", "--source", "desktop");

  if (resumeSessionId) {
    args.push("--resume", resumeSessionId);
  }

  if (mc.model) {
    args.push("-m", mc.model);
  }

  const env: Record<string, string> = {
    ...(buildQiqiclawEnv() as Record<string, string>),
    PYTHONUNBUFFERED: "1",
  };

  // Inject all API keys from the profile .env so the CLI can access them.
  // The built-in remote OpenAI-compatible providers (DeepSeek, Together,
  // Fireworks, Cerebras, Mistral) are listed here too — without them the
  // agent has no way to see the user-configured key when the user picked
  // the built-in provider entry rather than a `custom` entry, and the
  // upstream fallback chain then misroutes the request (see #260 / the
  // `pickAutoApiKeyForCustomProvider` workaround in config.ts).
  const KNOWN_API_KEYS = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "LM_API_KEY",
    "XIAOMI_API_KEY",
    "TOKENHUB_API_KEY",
    "NVIDIA_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "HF_TOKEN",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CODING_API_KEY",
    "KIMI_CN_API_KEY",
    "STEPFUN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "DASHSCOPE_API_KEY",
    "OLLAMA_API_KEY",
    "ARCEEAI_API_KEY",
    "GMI_API_KEY",
    "KILOCODE_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "OPENCODE_GO_API_KEY",
    "AZURE_FOUNDRY_API_KEY",
    "AZURE_FOUNDRY_BASE_URL",
    "AI_GATEWAY_API_KEY",
    "EXA_API_KEY",
    "PARALLEL_API_KEY",
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "FAL_KEY",
    "HONCHO_API_KEY",
    "BROWSERBASE_API_KEY",
    "BROWSERBASE_PROJECT_ID",
    "VOICE_TOOLS_OPENAI_KEY",
    "TINKER_API_KEY",
    "WANDB_API_KEY",
  ];
  for (const key of KNOWN_API_KEYS) {
    if (profileEnv[key] && !env[key]) {
      env[key] = profileEnv[key];
    }
  }

  const isCustomEndpoint = isOpenAICompatProvider(mc.provider);
  if (isCustomEndpoint && mc.baseUrl) {
    const inferenceBaseUrl = normaliseOpenAICompatBaseUrl(mc.baseUrl);
    // Check if this model has an explicit apiMode from custom_providers
    let modelApiMode: string | null = null;
    try {
      const modelEntry = readModels().find(
        (m) => m.baseUrl === mc.baseUrl && m.model === mc.model,
      );
      if (modelEntry) modelApiMode = modelEntry.apiMode || null;
    } catch {
      /* ignore */
    }
    const isAnthropicProtocol = modelApiMode === "anthropic_messages";
    if (isAnthropicProtocol) {
      env.QIQICLAW_INFERENCE_PROVIDER = "anthropic";
      env.ANTHROPIC_BASE_URL = inferenceBaseUrl;
    } else {
      env.QIQICLAW_INFERENCE_PROVIDER = "custom";
      env.OPENAI_BASE_URL = inferenceBaseUrl;
    }

    // Resolve the right API key: check URL-specific key first, then OPENAI_API_KEY
    let resolvedKey = "";
    for (const { pattern, envKey } of URL_KEY_MAP) {
      if (pattern.test(mc.baseUrl)) {
        resolvedKey = profileEnv[envKey] || env[envKey] || "";
        break;
      }
    }
    if (!resolvedKey) {
      resolvedKey = credentialPoolKeyForBaseUrl(mc.baseUrl, profile);
    }
    if (!resolvedKey) {
      // Try custom provider auto-generated key from models.json
      try {
        const models = readModels();
        const matching = models.find((m) => m.baseUrl === mc.baseUrl);
        if (matching) {
          const envKey2 =
            "CUSTOM_PROVIDER_" +
            matching.name.replace(/[^A-Za-z0-9]/g, "_").toUpperCase() +
            "_KEY";
          resolvedKey = profileEnv[envKey2] || env[envKey2] || "";
        }
      } catch {
        /* ignore */
      }
      if (!resolvedKey) {
        resolvedKey =
          profileEnv.CUSTOM_API_KEY ||
          env.CUSTOM_API_KEY ||
          profileEnv.OPENAI_API_KEY ||
          env.OPENAI_API_KEY ||
          "";
      }
    }
    // Local servers (localhost/127.0.0.1) don't need a real key
    if (!resolvedKey && /localhost|127\.0\.0\.1/i.test(mc.baseUrl)) {
      resolvedKey = "no-key-required";
    }
    if (isAnthropicProtocol) {
      env.ANTHROPIC_API_KEY = resolvedKey || "no-key-required";
    } else {
      env.OPENAI_API_KEY = resolvedKey || "no-key-required";
    }

    delete env.OPENROUTER_API_KEY;
    delete env.ANTHROPIC_TOKEN;
    delete env.OPENROUTER_BASE_URL;
  }

  const proc = spawn(QIQICLAW_PYTHON, args, {
    cwd: QIQICLAW_REPO,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    ...HIDDEN_SUBPROCESS_OPTIONS,
  });

  let hasOutput = false;
  let capturedSessionId = "";
  let outputBuffer = "";

  function captureSessionId(text: string): void {
    const sidMatch = text.match(/session_id:\s*(\S+)/);
    if (sidMatch) capturedSessionId = sidMatch[1];
  }

  function processOutput(raw: Buffer): void {
    const text = stripAnsi(raw.toString());
    outputBuffer += text;

    captureSessionId(outputBuffer);

    const cleaned = text.replace(/session_id:\s*\S+\n?/g, "");
    const lines = cleaned.split("\n");
    const result: string[] = [];
    for (const line of lines) {
      const t = line.trim();
      if (t && NOISE_PATTERNS.some((p) => p.test(t))) continue;
      result.push(line);
    }

    const output = result.join("\n");
    if (output) {
      hasOutput = true;
      cb.onChunk(output);
    }
  }

  proc.stdout?.on("data", processOutput);

  let stderrBuffer = "";
  proc.stderr?.on("data", (data: Buffer) => {
    const text = stripAnsi(data.toString());
    captureSessionId(text);
    if (
      !text.trim() ||
      text.includes("UserWarning") ||
      text.includes("FutureWarning")
    ) {
      return;
    }
    // Forward errors visibly to the chat
    if (
      /❌|⚠️|Error|Traceback|error|failed|denied|unauthorized|invalid/i.test(
        text,
      )
    ) {
      hasOutput = true;
      cb.onChunk(text);
    } else {
      // Buffer other stderr for reporting on non-zero exit
      stderrBuffer += text;
    }
  });

  proc.on("close", (code) => {
    if (code === 0 || hasOutput) {
      cb.onDone(capturedSessionId || undefined);
    } else {
      const detail = stderrBuffer.trim();
      cb.onError(
        detail
          ? `QiQiClaw exited with code ${code}: ${detail}`
          : `QiQiClaw exited with code ${code}. Check your model configuration and API key.`,
      );
    }
  });

  proc.on("error", (err) => {
    cb.onError(err.message);
  });

  return {
    abort: () => {
      proc.kill("SIGTERM");
      setTimeout(() => {
        if (!proc.killed) proc.kill("SIGKILL");
      }, 3000);
    },
  };
}

// ────────────────────────────────────────────────────
//  Public API: auto-routes to HTTP API or CLI fallback
// ────────────────────────────────────────────────────

let apiServerAvailable: boolean | null = null; // cached after first check

export async function sendMessage(
  message: string,
  cb: ChatCallbacks,
  profile?: string,
  resumeSessionId?: string,
  history?: Array<{ role: string; content: string }>,
  attachments?: Attachment[],
  contextFolder?: string,
): Promise<ChatHandle> {
  ensureInitialized();

  // Remote mode: always use API, no CLI fallback
  if (isRemoteMode()) {
    return sendMessageViaApi(
      message,
      cb,
      profile,
      resumeSessionId,
      history,
      attachments,
      contextFolder,
    );
  }

  // Check API server availability. In local mode, a running gateway process
  // can still be in its startup window (or the cached ready state can be stale
  // after an external stop/start), so verify health before taking the API path.
  const localGatewayRunning = !isRemoteMode() && isGatewayRunning();
  apiServerAvailable = await isApiServerReady();
  if (!apiServerAvailable && localGatewayRunning) {
    apiServerAvailable = await waitForApiServerReady();
  }

  if (apiServerAvailable) {
    return sendMessageViaApi(
      message,
      cb,
      profile,
      resumeSessionId,
      history,
      attachments,
      contextFolder,
    );
  }

  // Fallback to CLI
  return sendMessageViaCli(message, cb, profile, resumeSessionId, attachments);
}

// Lazy init — called on first sendMessage or gateway start
let _initialized = false;
let _healthCheckInterval: ReturnType<typeof setInterval> | null = null;

function ensureInitialized(): void {
  if (_initialized) return;
  _initialized = true;
  if (!isRemoteMode()) {
    ensureApiServerConfig();
  }
  startHealthPolling();
}

function startHealthPolling(): void {
  if (_healthCheckInterval) return;
  _healthCheckInterval = setInterval(async () => {
    apiServerAvailable = await isApiServerReady();
    // Stop polling once API is confirmed available — only re-check on demand
    if (apiServerAvailable && _healthCheckInterval) {
      clearInterval(_healthCheckInterval);
      _healthCheckInterval = null;
    }
  }, 15000);
}

export function stopHealthPolling(): void {
  if (_healthCheckInterval) {
    clearInterval(_healthCheckInterval);
    _healthCheckInterval = null;
  }
}

// ────────────────────────────────────────────────────
//  Gateway management
// ────────────────────────────────────────────────────

let gatewayProcess: ChildProcess | null = null;
let gatewayStartedByApp = false;
let gatewayStartInFlight: Promise<boolean> | null = null;

export function startGateway(profile?: string): boolean {
  // Defensive: the local gateway is never the right thing to spawn in
  // remote/SSH mode — the user is pointing at an off-machine server.
  // Callers should already gate, but several IPC handlers historically
  // forgot to (issue #266), and reaching `spawn(QIQICLAW_PYTHON, …)` when
  // there's no local QiQiClaw install produces an uncaught ENOENT
  // that pops a generic error dialog.  Refuse cleanly here.
  if (isRemoteMode()) {
    console.warn(
      "[gateway] startGateway() called in remote/SSH mode — refusing local spawn",
    );
    return false;
  }
  ensureInitialized();
  if (gatewayProcess && !gatewayProcess.killed) return false;

  const missingBackend = localBackendMissingError();
  if (missingBackend) {
    console.error(`[gateway] Cannot start: ${missingBackend}`);
    return false;
  }

  // Pre-flight: verify the Python interpreter exists before attempting to
  // spawn. Without this check, spawn() fails with ENOENT and the error is
  // completely silent (stdio:"ignore", no error handler).
  if (!existsSync(QIQICLAW_PYTHON)) {
    console.error(
      `[gateway] Cannot start: Python interpreter not found at ${QIQICLAW_PYTHON}. ` +
        "Is QiQiClaw installed?",
    );
    return false;
  }
  if (!existsSync(QIQICLAW_REPO)) {
    console.error(
      `[gateway] Cannot start: QiQiClaw engine files not found at ${QIQICLAW_REPO}. ` +
        "Is QiQiClaw installed?",
    );
    return false;
  }

  // Build gateway env with profile API keys
  const gatewayEnv: Record<string, string> = {
    ...(buildQiqiclawEnv() as Record<string, string>),
    API_SERVER_ENABLED: "true", // Ensure API server starts with gateway
    API_SERVER_HOST: QIQICLAW_GATEWAY_HOST,
    API_SERVER_PORT: QIQICLAW_GATEWAY_PORT,
  };

  // Inject ALL profile API keys so the gateway can authenticate with any provider.
  const profileEnv = readEnv(profile);
  for (const [key, value] of Object.entries(profileEnv)) {
    if (value) {
      gatewayEnv[key] = value;
    }
  }

  // Route stderr to a log file so startup errors are visible for debugging.
  // stdout is still ignored (the gateway daemonizes and writes its own logs).
  const logDir = QIQICLAW_HOME;
  try {
    mkdirSync(logDir, { recursive: true });
  } catch {
    // ignore
  }
  let stderrFd: number | "ignore" = "ignore";
  try {
    stderrFd = openSync(GATEWAY_STDERR_LOG, "a");
  } catch (err) {
    console.error(
      `[gateway] Could not open ${GATEWAY_STDERR_LOG} for stderr logging:`,
      err instanceof Error ? err.message : String(err),
    );
  }
  let stderrFdClosed = false;
  const closeGatewayStderrFd = (): void => {
    if (stderrFdClosed || typeof stderrFd !== "number") return;
    stderrFdClosed = true;
    try {
      closeSync(stderrFd);
    } catch {
      // ignore
    }
  };

  gatewayProcess = spawn(
    QIQICLAW_PYTHON,
    qiqiclawCliArgs(["gateway", "--accept-hooks", "run"]),
    {
      cwd: QIQICLAW_REPO,
      env: gatewayEnv,
      stdio: ["ignore", "ignore", stderrFd],
      detached: true,
      ...HIDDEN_SUBPROCESS_OPTIONS,
    },
  );

  gatewayProcess.on("error", (err) => {
    console.error("[gateway] Failed to spawn gateway process:", err.message);
    closeGatewayStderrFd();
    gatewayProcess = null;
    gatewayStartedByApp = false;
    apiServerAvailable = false;
  });

  gatewayProcess.on("close", (code, signal) => {
    closeGatewayStderrFd();
    if (code !== null && code !== 0) {
      console.error(
        `[gateway] Process exited with code ${code}${signal ? ` (signal: ${signal})` : ""}. ` +
          `Check ${GATEWAY_STDERR_LOG} for details.`,
      );
    }
    gatewayProcess = null;
    gatewayStartedByApp = false;
    apiServerAvailable = false;
    // Restart health polling to detect if gateway comes back
    startHealthPolling();
  });

  gatewayProcess.unref();
  gatewayStartedByApp = true;

  // Wait a bit then check if API server came up
  setTimeout(async () => {
    apiServerAvailable = await isApiServerReady();
  }, 3000);

  return true;
}

function parsePidFromFile(pidFile: string): number | null {
  if (!existsSync(pidFile)) return null;
  try {
    const raw = readFileSync(pidFile, "utf-8").trim();
    // PID file can be JSON ({"pid": 1234, ...}) or plain integer
    const parsed = raw.startsWith("{")
      ? JSON.parse(raw).pid
      : parseInt(raw, 10);
    return typeof parsed === "number" && !isNaN(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Returns candidate gateway.pid paths to check. The hermes CLI writes the
 * PID file into the active profile's home directory when a named profile is
 * in use (e.g. ~/.qiqiclaw/profiles/fatha/gateway.pid), falling back to
 * ~/.qiqiclaw/gateway.pid for the default profile. We check both so that
 * isGatewayRunning() works regardless of which profile is active.
 */
function gatewayPidPaths(): string[] {
  const paths: string[] = [join(QIQICLAW_HOME, "gateway.pid")];
  const activeProfile = getActiveProfileNameSync();
  if (activeProfile && activeProfile !== "default") {
    paths.push(join(profileHome(activeProfile), "gateway.pid"));
  }
  return paths;
}

function readPidFile(): number | null {
  for (const pidFile of gatewayPidPaths()) {
    const pid = parsePidFromFile(pidFile);
    if (pid !== null) return pid;
  }
  return null;
}

export function stopGateway(force = false): void {
  if (!force && !gatewayStartedByApp) return;

  if (gatewayProcess && !gatewayProcess.killed) {
    gatewayProcess.kill("SIGTERM");
    gatewayProcess = null;
  }
  const pid = readPidFile();
  if (pid) {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // already dead
    }
  }
  // Always clear the PID file once we've signalled it. Leaving a stale PID
  // around means the next isGatewayRunning() / stopGateway() call can hit
  // an unrelated process that the OS has since assigned the same PID.
  for (const pidFile of gatewayPidPaths()) {
    if (existsSync(pidFile)) {
      try {
        unlinkSync(pidFile);
      } catch {
        // best-effort; will be overwritten on next gateway start
      }
    }
  }
  gatewayStartedByApp = false;
  apiServerAvailable = false;
}

// Python image prefixes covering both native Windows (pythonw.exe / python.exe)
// and POSIX (python, python3, pythonw). Used to verify the PID we read from
// gateway.pid actually belongs to a python process before reporting alive.
const GATEWAY_IMAGE_PREFIXES = ["python", "pythonw"];

export function isGatewayRunning(): boolean {
  if (gatewayProcess && !gatewayProcess.killed) return true;
  const pid = readPidFile();
  if (!pid) return false;
  return pidIsAliveAs(pid, GATEWAY_IMAGE_PREFIXES);
}

export function isApiReady(): boolean {
  return apiServerAvailable === true;
}

export function testRemoteConnection(
  url: string,
  apiKey?: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    const target = `${coerceQiqiclawGatewayUrl(url)}/health`;
    const mod = target.startsWith("https") ? https : http;
    const headers: Record<string, string> = {};
    const resolvedApiKey = resolveRemoteApiKey(url, apiKey);
    if (resolvedApiKey) headers.Authorization = `Bearer ${resolvedApiKey}`;
    const req = mod.request(
      target,
      { method: "GET", timeout: 5000, headers },
      (res) => {
        resolve(res.statusCode === 200);
        res.resume();
      },
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

export async function ensureLocalApiUsable(
  profile?: string,
  timeoutMs = API_READY_TIMEOUT_MS,
): Promise<{ ok: boolean; error?: string }> {
  if (isRemoteMode()) return { ok: true };
  ensureInitialized();

  const missingBackend = localBackendMissingError();
  if (missingBackend) {
    return { ok: false, error: missingBackend };
  }

  if (gatewayStartInFlight) {
    const ok = await gatewayStartInFlight;
    if (!ok) {
      return { ok: false, error: gatewayNotReadyError() };
    }
  } else if (!isGatewayRunning()) {
    const started = startGateway(profile);
    if (!started && !isGatewayRunning()) {
      return { ok: false, error: gatewayNotReadyError() };
    }
  }

  if (!apiServerAvailable) {
    gatewayStartInFlight = waitForApiServerReady(timeoutMs).finally(() => {
      gatewayStartInFlight = null;
    });
    const ready = await gatewayStartInFlight;
    if (!ready) {
      return { ok: false, error: gatewayNotReadyError() };
    }
  }

  const apiServerKey = getApiServerKey(profile);
  const headers: Record<string, string> = {};
  if (apiServerKey) headers.Authorization = `Bearer ${apiServerKey}`;

  const checkModels = (): Promise<{
    ok: boolean;
    error?: string;
    connectionRefused?: boolean;
  }> =>
    new Promise((resolve) => {
      const req = http.request(
        `${LOCAL_API_URL}/v1/models`,
        { method: "GET", timeout: 5000, headers },
        (res) => {
          let body = "";
          res.setEncoding("utf-8");
          res.on("data", (chunk) => {
            body += chunk;
          });
          res.on("end", () => {
            if (res.statusCode !== 200) {
              resolve({
                ok: false,
                error: `QiQiClaw API /v1/models returned HTTP ${res.statusCode}.`,
              });
              return;
            }
            try {
              const parsed = JSON.parse(body) as { data?: unknown };
              if (Array.isArray(parsed.data)) {
                apiServerAvailable = true;
                resolve({ ok: true });
                return;
              }
            } catch {
              /* handled below */
            }
            resolve({
              ok: false,
              error: "QiQiClaw API /v1/models returned an invalid response.",
            });
          });
        },
      );
      req.on("error", (err) => {
        resolve({
          ok: false,
          error: `QiQiClaw API request failed: ${err.message}`,
          connectionRefused: isConnectionRefusedError(err),
        });
      });
      req.on("timeout", () => {
        req.destroy();
        resolve({ ok: false, error: "QiQiClaw API /v1/models timed out." });
      });
      req.end();
    });

  const first = await checkModels();
  if (first.ok || !first.connectionRefused) return first;

  apiServerAvailable = false;
  // A refused local API can mean either the gateway is not running, or a stale
  // gateway process is alive without the API server enabled/listening. Force a
  // local restart so the desktop-controlled env pins 127.0.0.1:8642.
  stopGateway(true);
  startGateway(profile);
  gatewayStartInFlight = waitForApiServerReady(timeoutMs).finally(() => {
    gatewayStartInFlight = null;
  });
  const recovered = await gatewayStartInFlight;
  if (!recovered) {
    return { ok: false, error: gatewayNotReadyError() };
  }
  return checkModels();
}

export function restartGateway(profile?: string): void {
  // Same defensive gate as startGateway — the local gateway has no role
  // in remote/SSH mode.  Cheap to check; catches IPC paths that don't
  // wrap their restart calls in an isRemoteMode() check.
  if (isRemoteMode()) return;
  if (!gatewayStartedByApp && !isGatewayRunning()) return;
  stopGateway(true);
  setTimeout(() => {
    startGateway(profile);
  }, 500);
}

export function restartInstalledGateway(profile?: string): Promise<boolean> {
  if (isRemoteMode()) return Promise.resolve(false);
  if (!existsSync(QIQICLAW_PYTHON) || !existsSync(QIQICLAW_REPO)) {
    return Promise.resolve(false);
  }

  const args = qiqiclawCliArgs();
  if (profile && profile !== "default") {
    args.push("-p", profile);
  }
  args.push("gateway", "restart");

  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      args,
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv({ TERM: "dumb" }),
        timeout: 45000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error, stdout, stderr) => {
        if (error) {
          console.error(
            "[gateway] CLI restart failed:",
            stripAnsi(stderr || stdout || error.message).slice(0, 500),
          );
          resolve(false);
          return;
        }
        apiServerAvailable = false;
        startHealthPolling();
        resolve(true);
      },
    );
  });
}

export function stopInstalledGatewayService(): Promise<boolean> {
  if (isRemoteMode() || process.platform === "win32") {
    return Promise.resolve(false);
  }

  return new Promise((resolve) => {
    execFile(
      "systemctl",
      ["--user", "stop", "qiqiclaw-gateway.service"],
      {
        env: buildQiqiclawEnv(),
        timeout: 15000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error) => {
        resolve(!error);
      },
    );
  });
}
