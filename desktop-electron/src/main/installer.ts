import { spawn, execFile, execFileSync } from "child_process";
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
  unlinkSync,
} from "fs";
import { join, delimiter, resolve } from "path";
import { homedir, tmpdir } from "os";
import { randomBytes } from "crypto";
import { app, type BrowserWindow } from "electron";
import {
  getConnectionConfig,
  getModelConfig,
  hasOAuthCredentials,
} from "./config";
import { providerDoesNotNeedApiKey } from "./providers";
import { getActiveProfileNameSync, profileHome, stripAnsi } from "./utils";
import { setupAskpass, AskpassHandle } from "./askpass";
import { precacheSudoCredentials } from "./sudoCreds";
import { HIDDEN_SUBPROCESS_OPTIONS } from "./process-options";
import { getYamlPath } from "./yaml-path";

const IS_WINDOWS = process.platform === "win32";

// Resolve the QiQiClaw data directory. Precedence:
//   1. QIQICLAW_HOME env var if set (install.ps1 sets it User-scope on
//      Windows; users may also override manually for WSL/custom setups).
//   2. On Windows, prefer ~/.qiqiclaw, matching install.ps1.
//   3. Fresh install fallback: ~/.qiqiclaw on every platform.
//
// Motivating bug: Electron launched from the Start Menu doesn't always
// inherit shell-set env vars, so the desktop must resolve the same default
// home the installer writes.
function looksLikeQiqiclawHome(dir: string): boolean {
  if (!existsSync(dir)) return false;
  return (
    existsSync(join(dir, "qiqiclaw")) ||
    existsSync(join(dir, "gateway.pid")) ||
    existsSync(join(dir, "config.yaml")) ||
    existsSync(join(dir, "active_profile")) ||
    existsSync(join(dir, ".env"))
  );
}

function defaultQiqiclawHome(): string {
  const homeDot = join(homedir(), ".qiqiclaw");
  if (!IS_WINDOWS) return homeDot;
  if (looksLikeQiqiclawHome(homeDot)) return homeDot;

  // Neither populated yet — fall back to install.ps1's default so a
  // fresh install lines up with where the installer will write.
  return homeDot;
}

// A QiQiClaw home the user explicitly pointed the app at via the "use an
// existing installation" flow (issue #272). Persisted in the desktop's own
// userData dir — outside any QiQiClaw home — so it can be read here, before
// QIQICLAW_HOME is resolved. Strictly additive: with no override file the
// behaviour is identical to before.
function qiqiclawHomeOverrideFile(): string {
  // `app` is undefined outside an Electron runtime (e.g. unit tests) —
  // optional-chain it so module load degrades to "no override" instead of
  // throwing.
  const userData = app?.getPath?.("userData");
  return userData ? join(userData, "qiqiclaw-home.json") : "";
}

function readQiqiclawHomeOverride(): string {
  try {
    const file = qiqiclawHomeOverrideFile();
    if (!file || !existsSync(file)) return "";
    const parsed = JSON.parse(readFileSync(file, "utf-8")) as {
      qiqiclawHome?: unknown;
    };
    const p =
      typeof parsed.qiqiclawHome === "string" ? parsed.qiqiclawHome.trim() : "";
    // Ignore a stale override whose directory no longer exists.
    return p && existsSync(p) ? p : "";
  } catch {
    return "";
  }
}

/** Persist (when `home` is set) or clear (when "") the QiQiClaw home override. */
export function setQiqiclawHomeOverride(home: string): void {
  try {
    const file = qiqiclawHomeOverrideFile();
    if (!file) return;
    if (!home.trim()) {
      if (existsSync(file)) unlinkSync(file);
      return;
    }
    writeFileSync(
      file,
      JSON.stringify({ qiqiclawHome: home.trim() }, null, 2),
      "utf-8",
    );
  } catch {
    /* best effort — a failed write just means no override next launch */
  }
}

export const QIQICLAW_HOME =
  process.env.QIQICLAW_HOME?.trim() ||
  readQiqiclawHomeOverride() ||
  defaultQiqiclawHome();
export const QIQICLAW_REPO = join(QIQICLAW_HOME, "qiqiclaw");
export const QIQICLAW_VENV = join(QIQICLAW_REPO, "venv");
// On Windows, use `pythonw.exe` (the GUI-subsystem interpreter that ships in
// every venv) instead of `python.exe` so that subprocess spawns don't flash
// a blank console window before `windowsHide: true` / CREATE_NO_WINDOW takes
// effect. Issue #342: on every chat send the `sendMessageViaCli` fallback
// path spawned `python.exe`, and the console appeared for a few hundred ms
// despite `windowsHide: true` — a well-known race between console allocation
// and CREATE_NO_WINDOW on console-subsystem child binaries. `pythonw.exe`
// is linked as Windows subsystem, so the OS can never allocate a console
// for it regardless of creation flags. It's a bit-identical interpreter
// otherwise — same modules, same stdout/stderr behaviour over piped stdio
// (which is what every call site here uses).
export const QIQICLAW_PYTHON = IS_WINDOWS
  ? join(QIQICLAW_VENV, "Scripts", "pythonw.exe")
  : join(QIQICLAW_VENV, "bin", "python");
export const QIQICLAW_SCRIPT = IS_WINDOWS
  ? join(QIQICLAW_VENV, "Scripts", "qiqiclaw.exe")
  : join(QIQICLAW_REPO, "qiqiclaw");
export const QIQICLAW_ENV_FILE = join(QIQICLAW_HOME, ".env");
export const QIQICLAW_CONFIG_FILE = join(QIQICLAW_HOME, "config.yaml");
export const QIQICLAW_AUTH_FILE = join(QIQICLAW_HOME, "auth.json");

function executableExists(path: string): boolean {
  if (!existsSync(path)) return false;
  if (IS_WINDOWS) return true;
  try {
    return (statSync(path).mode & 0o111) !== 0;
  } catch {
    return false;
  }
}

function cliCandidatesFor(repo: string): string[] {
  return IS_WINDOWS
    ? [join(repo, "qiqiclaw.cmd"), join(repo, "venv", "Scripts", "qiqiclaw.exe")]
    : [join(repo, "qiqiclaw"), join(repo, "venv", "bin", "qiqiclaw")];
}

function resolveCliScript(repo: string = QIQICLAW_REPO): string {
  return (
    cliCandidatesFor(repo).find(executableExists) || cliCandidatesFor(repo)[0]
  );
}

export function hasQiqiclawCli(repo: string = QIQICLAW_REPO): boolean {
  return cliCandidatesFor(repo).some(executableExists);
}

/** The Python + qiqiclaw launcher paths for a QiQiClaw install rooted at `home`,
 *  in the layout the desktop's own installer produces. */
function installBinariesFor(home: string): { python: string; script: string } {
  const repo = join(home, "qiqiclaw");
  const venv = join(repo, "venv");
  const script = resolveCliScript(repo);
  return IS_WINDOWS
    ? {
        python: join(venv, "Scripts", "python.exe"),
        script,
      }
    : { python: join(venv, "bin", "python"), script };
}

export function qiqiclawCliArgs(args: string[] = []): string[] {
  if (process.platform === "win32") {
    return ["-m", "qiqiclaw_cli.main", ...args];
  }
  return [resolveCliScript(), ...args];
}

export function buildQiqiclawEnv(
  extra: NodeJS.ProcessEnv = {},
): NodeJS.ProcessEnv {
  return {
    ...process.env,
    PATH: getEnhancedPath(),
    HOME: homedir(),
    QIQICLAW_HOME: QIQICLAW_HOME,
    ...extra,
  };
}

export interface InstallStatus {
  installed: boolean;
  configured: boolean;
  hasApiKey: boolean;
  verified: boolean;
  activeProfile?: string;
}

export interface InstallProgress {
  step: number;
  totalSteps: number;
  title: string;
  detail: string;
  log: string;
}

export function getEnhancedPath(): string {
  const home = homedir();
  const extra = (
    IS_WINDOWS
      ? [
          // Bundled by install.ps1 inside QIQICLAW_HOME — these matter when the
          // user's system PATH doesn't include git or node yet.
          join(QIQICLAW_HOME, "git", "bin"),
          join(QIQICLAW_HOME, "git", "cmd"),
          join(QIQICLAW_HOME, "git", "usr", "bin"),
          join(QIQICLAW_HOME, "node"),
          join(QIQICLAW_VENV, "Scripts"),
          // Common user/system installs used when Claw3D setup runs before or
          // outside the bundled installer.
          process.env.NVM_SYMLINK,
          process.env.APPDATA ? join(process.env.APPDATA, "npm") : undefined,
          process.env.ProgramFiles
            ? join(process.env.ProgramFiles, "nodejs")
            : undefined,
          process.env["ProgramFiles(x86)"]
            ? join(process.env["ProgramFiles(x86)"], "nodejs")
            : undefined,
          process.env.ProgramFiles
            ? join(process.env.ProgramFiles, "Git", "cmd")
            : undefined,
          process.env.LOCALAPPDATA
            ? join(process.env.LOCALAPPDATA, "Programs", "Git", "cmd")
            : undefined,
          // Where `uv` lands when astral.sh's installer runs.
          join(home, ".local", "bin"),
          join(home, ".cargo", "bin"),
        ]
      : [
          join(home, ".local", "bin"),
          join(home, ".cargo", "bin"),
          join(QIQICLAW_VENV, "bin"),
          // Node version manager shim directories
          join(home, ".volta", "bin"),
          join(home, ".asdf", "shims"),
          join(home, ".local", "share", "fnm", "aliases", "default", "bin"),
          join(home, ".fnm", "aliases", "default", "bin"),
          ...resolveNvmBin(home),
          "/usr/local/bin",
          "/opt/homebrew/bin",
          "/opt/homebrew/sbin",
        ]
  ).filter((entry): entry is string => Boolean(entry));
  return [...extra, process.env.PATH || ""].filter(Boolean).join(delimiter);
}

/** Resolve the active nvm node version's bin directory. */
function resolveNvmBin(home: string): string[] {
  const nvmDir = process.env.NVM_DIR || join(home, ".nvm");
  const versionsDir = join(nvmDir, "versions", "node");
  if (!existsSync(versionsDir)) return [];
  try {
    // Try to read the default alias to find the active version
    const aliasFile = join(nvmDir, "alias", "default");
    if (existsSync(aliasFile)) {
      const alias = readFileSync(aliasFile, "utf-8").trim();
      // alias can be a full version "v20.11.0" or a partial "20" or "lts/*"
      if (alias.startsWith("v")) {
        const bin = join(versionsDir, alias, "bin");
        if (existsSync(bin)) return [bin];
      }
    }
    // Fallback: pick the latest installed version
    const versions = (readdirSync(versionsDir) as string[])
      .filter((d: string) => d.startsWith("v"))
      .sort()
      .reverse();
    if (versions.length > 0) {
      return [join(versionsDir, versions[0], "bin")];
    }
  } catch {
    /* non-fatal */
  }
  return [];
}

function activeEnvFile(profile: string): string {
  return profile === "default"
    ? QIQICLAW_ENV_FILE
    : join(QIQICLAW_HOME, "profiles", profile, ".env");
}

function activeAuthFile(profile: string): string {
  return profile === "default"
    ? QIQICLAW_AUTH_FILE
    : join(QIQICLAW_HOME, "profiles", profile, "auth.json");
}

// Canonical env-var name per known model provider. Keys here are values
// the user might see in `model.provider` in config.yaml; values are the
// env vars the gateway expects to read from .env. Names that don't
// appear here either don't need a key (local providers, nous) or have
// OAuth-style credentials are covered separately through auth.json.
//
// Used by the install-gate check below. Previously that check
// hard-coded only OPENROUTER_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY,
// so any user configured for DeepSeek, Groq, Mistral, etc. saw the
// "set AI provider" first-run screen even with a valid key in .env.
// See issue #236.
const PROVIDER_ENV_KEYS: Record<string, string> = {
  openrouter: "OPENROUTER_API_KEY",
  lmstudio: "LM_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  xiaomi: "XIAOMI_API_KEY",
  "tencent-tokenhub": "TOKENHUB_API_KEY",
  nvidia: "NVIDIA_API_KEY",
  copilot: "COPILOT_GITHUB_TOKEN",
  huggingface: "HF_TOKEN",
  hf: "HF_TOKEN",
  gemini: "GOOGLE_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  xai: "XAI_API_KEY",
  zai: "GLM_API_KEY",
  glm: "GLM_API_KEY",
  "kimi-coding": "KIMI_API_KEY",
  "kimi-coding-cn": "KIMI_CN_API_KEY",
  kimi: "KIMI_API_KEY",
  stepfun: "STEPFUN_API_KEY",
  minimax: "MINIMAX_API_KEY",
  "minimax-cn": "MINIMAX_CN_API_KEY",
  alibaba: "DASHSCOPE_API_KEY",
  "ollama-cloud": "OLLAMA_API_KEY",
  arcee: "ARCEEAI_API_KEY",
  gmi: "GMI_API_KEY",
  kilocode: "KILOCODE_API_KEY",
  "opencode-zen": "OPENCODE_ZEN_API_KEY",
  "opencode-go": "OPENCODE_GO_API_KEY",
  "azure-foundry": "AZURE_FOUNDRY_API_KEY",
  "ai-gateway": "AI_GATEWAY_API_KEY",
};

// When provider is "custom" or "auto", the desktop's setup flow falls
// back to recognizing the endpoint by base URL. Same patterns hermes.ts
// uses for runtime header injection.
const URL_TO_ENV_KEY: Array<[RegExp, string]> = [
  [/openrouter\.ai/i, "OPENROUTER_API_KEY"],
  [/anthropic\.com/i, "ANTHROPIC_API_KEY"],
  [/openai\.com/i, "OPENAI_API_KEY"],
  [/xiaomimimo\.com/i, "XIAOMI_API_KEY"],
  [/tokenhub\.tencentmaas\.com/i, "TOKENHUB_API_KEY"],
  [/integrate\.api\.nvidia\.com/i, "NVIDIA_API_KEY"],
  [/api\.githubcopilot\.com/i, "COPILOT_GITHUB_TOKEN"],
  [/huggingface\.co/i, "HF_TOKEN"],
  [/generativelanguage\.googleapis\.com/i, "GOOGLE_API_KEY"],
  [/api\.deepseek\.com/i, "DEEPSEEK_API_KEY"],
  [/api\.groq\.com/i, "GROQ_API_KEY"],
  [/api\.x\.ai/i, "XAI_API_KEY"],
  [/api\.z\.ai/i, "GLM_API_KEY"],
  [/api\.moonshot\.ai/i, "KIMI_API_KEY"],
  [/api\.moonshot\.cn/i, "KIMI_CN_API_KEY"],
  [/api\.stepfun\.ai/i, "STEPFUN_API_KEY"],
  [/api\.minimax\.io/i, "MINIMAX_API_KEY"],
  [/api\.minimaxi\.com/i, "MINIMAX_CN_API_KEY"],
  [/dashscope.*aliyuncs\.com/i, "DASHSCOPE_API_KEY"],
  [/ollama\.com/i, "OLLAMA_API_KEY"],
  [/api\.arcee\.ai/i, "ARCEEAI_API_KEY"],
  [/api\.gmi-serving\.com/i, "GMI_API_KEY"],
  [/api\.kilo\.ai/i, "KILOCODE_API_KEY"],
  [/opencode\.ai\/zen\/go/i, "OPENCODE_GO_API_KEY"],
  [/opencode\.ai\/zen/i, "OPENCODE_ZEN_API_KEY"],
  [/ai-gateway\.vercel\.sh/i, "AI_GATEWAY_API_KEY"],
  [/api\.together\.xyz/i, "TOGETHER_API_KEY"],
  [/api\.fireworks\.ai/i, "FIREWORKS_API_KEY"],
  [/api\.cerebras\.ai/i, "CEREBRAS_API_KEY"],
  [/api\.mistral\.ai/i, "MISTRAL_API_KEY"],
  [/api\.perplexity\.ai/i, "PERPLEXITY_API_KEY"],
];

/**
 * Resolve the env var name the gateway expects for a given model config.
 * Returns null when the provider/URL combination has no known canonical
 * env var (the caller falls back to a permissive `*_API_KEY|*_TOKEN`
 * scan, matching the spirit of the prior hard-coded check).
 *
 * Exported for unit testing.
 */
export function expectedEnvKeyForModel(
  provider: string,
  baseUrl: string,
): string | null {
  const direct = PROVIDER_ENV_KEYS[provider.trim().toLowerCase()];
  if (direct) return direct;
  for (const [pattern, envKey] of URL_TO_ENV_KEY) {
    if (pattern.test(baseUrl)) return envKey;
  }
  return null;
}

function envHasUsableValue(
  content: string,
  expectedKey: string | null,
): boolean {
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const m = trimmed.match(/^([A-Z][A-Z0-9_]*)=(.*)$/);
    if (!m) continue;
    const key = m[1];
    let value = m[2].trim();
    // Strip surrounding quotes so `KEY=""` or `KEY="abc"` parse the
    // same way as `KEY=abc`.
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!value) continue;

    if (expectedKey) {
      if (key === expectedKey) return true;
    } else {
      // No known mapping for this provider/URL — accept any value that
      // looks like a credential. Avoids regressing users on providers
      // we haven't catalogued explicitly, while still rejecting
      // unrelated env vars (TELEGRAM_BOT_TOKEN etc. shouldn't satisfy
      // the model install gate, but a custom `*_API_KEY` should).
      if (/_API_KEY$/.test(key)) return true;
    }
  }
  return false;
}

// ── Pre-install inspection (issue #272) ──────────────────────────────────────

export type InstallTargetState = "fresh" | "update" | "replace";

export interface InstallTargetInfo {
  /** Where the desktop will install — shown to the user before they commit. */
  qiqiclawHome: string;
  repoPath: string;
  /** What the installer will do to `repoPath`:
   *  - `fresh`   — nothing is there; a clean install.
   *  - `update`  — a valid git checkout; install.sh/ps1 updates it in place.
   *  - `replace` — a directory is there but not a valid checkout, so the
   *                install script deletes and re-clones it. */
  state: InstallTargetState;
}

/** Classify what the installer will do to the target directory. Pure — the
 *  filesystem probing lives in `inspectInstallTarget`. */
export function classifyInstallTarget(
  repoExists: boolean,
  repoIsGitRepo: boolean,
): InstallTargetState {
  if (!repoExists) return "fresh";
  return repoIsGitRepo ? "update" : "replace";
}

/** Inspect the install target so the renderer can warn before installing. */
export function inspectInstallTarget(): InstallTargetInfo {
  const repoExists = existsSync(QIQICLAW_REPO);
  const repoIsGitRepo = repoExists && existsSync(join(QIQICLAW_REPO, ".git"));
  return {
    qiqiclawHome: QIQICLAW_HOME,
    repoPath: QIQICLAW_REPO,
    state: classifyInstallTarget(repoExists, repoIsGitRepo),
  };
}

/** True when `dir` is a QiQiClaw home the desktop can drive as-is — it must
 *  contain a `qiqiclaw` install with the venv binaries in the layout the
 *  desktop expects. A hand-rolled install with a different layout fails here
 *  rather than being silently adopted into a broken state (issue #272). */
export function validateQiqiclawHome(dir: string): boolean {
  const home = dir?.trim();
  if (!home || !existsSync(home)) return false;
  const { python, script } = installBinariesFor(home);
  return existsSync(python) && existsSync(script);
}

export function checkInstallStatus(): InstallStatus {
  const activeProfile = getActiveProfileNameSync();

  // Remote mode: skip local checks entirely
  const conn = getConnectionConfig();
  if (conn.mode === "remote" && conn.remoteUrl) {
    return {
      installed: true,
      configured: true,
      hasApiKey: true,
      verified: true,
      activeProfile,
    };
  }

  // Fast path: file existence is enough to gate the UI. The deep
  // `python --version` check used to run here adds 1–10s of cold-start
  // latency, so it now lives in `verifyInstall()` and is invoked lazily
  // by the renderer after the main UI is mounted.
  const installed = existsSync(QIQICLAW_PYTHON) && hasQiqiclawCli();
  const envFile = activeEnvFile(activeProfile);
  const authFile = activeAuthFile(activeProfile);
  const configured = existsSync(envFile) || existsSync(authFile);
  let hasApiKey = false;
  const verified = installed;

  // Local/custom providers don't need an API key. OAuth-backed providers
  // (including credential-pool entries) can be configured through QiQiClaw
  // auth.json instead of .env, so check those before falling back to keys.
  let mc: { provider: string; model: string; baseUrl: string } | null = null;
  try {
    mc = getModelConfig(activeProfile);
    if (
      providerDoesNotNeedApiKey(mc.provider) ||
      hasOAuthCredentials(mc.provider, activeProfile)
    ) {
      hasApiKey = true;
    }
  } catch {
    /* ignore */
  }

  if (!hasApiKey && configured && existsSync(envFile)) {
    try {
      const content = readFileSync(envFile, "utf-8");
      const expectedKey = mc
        ? expectedEnvKeyForModel(mc.provider, mc.baseUrl)
        : null;
      hasApiKey = envHasUsableValue(content, expectedKey);
    } catch {
      /* ignore read errors */
    }
  }

  return { installed, configured, hasApiKey, verified, activeProfile };
}

// Lazy background verification: actually invoke Python to confirm the
// install runs. Called from the renderer after the UI is already up.
let _verifyCache: { ok: boolean; ts: number } | null = null;
const VERIFY_TTL_MS = 5 * 60 * 1000;

export async function verifyInstall(): Promise<boolean> {
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) return false;
  if (_verifyCache && Date.now() - _verifyCache.ts < VERIFY_TTL_MS) {
    return _verifyCache.ok;
  }
  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      qiqiclawCliArgs(["--version"]),
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv(),
        timeout: 15000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error) => {
        const ok = !error;
        _verifyCache = { ok, ts: Date.now() };
        resolve(ok);
      },
    );
  });
}

// Cached version to avoid re-running the Python process
let _cachedVersion: string | null = null;
let _versionFetching = false;

export async function getHermesVersion(): Promise<string | null> {
  if (_cachedVersion !== null) return _cachedVersion;
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) return null;
  if (_versionFetching) {
    // Wait for the in-flight fetch but cap the wait. The execFile below
    // has a 15s timeout and its callback unconditionally clears
    // `_versionFetching`, so under normal failure paths the poll
    // unblocks on its own. Pathological cases (callback never invoked,
    // worker killed mid-callback, async exception in handler) would
    // otherwise leak a 100 ms interval per caller forever. Cap at 20s
    // — comfortably above the execFile timeout — and resolve with
    // whatever `_cachedVersion` happens to be (typically `null`),
    // which matches the same return shape callers already handle.
    return new Promise((resolve) => {
      const startedAt = Date.now();
      const check = setInterval(() => {
        if (!_versionFetching || Date.now() - startedAt > 20_000) {
          clearInterval(check);
          resolve(_cachedVersion);
        }
      }, 100);
    });
  }
  _versionFetching = true;
  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      qiqiclawCliArgs(["--version"]),
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv(),
        timeout: 15000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error, stdout) => {
        _versionFetching = false;
        if (error) {
          resolve(null);
        } else {
          _cachedVersion = stdout.toString().trim();
          resolve(_cachedVersion);
        }
      },
    );
  });
}

export function clearVersionCache(): void {
  _cachedVersion = null;
}

export function runHermesDoctor(): string {
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) {
    return "QiQiClaw is not installed.";
  }
  try {
    const output = execFileSync(QIQICLAW_PYTHON, qiqiclawCliArgs(["doctor"]), {
      cwd: QIQICLAW_REPO,
      env: buildQiqiclawEnv(),
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 30000,
      ...HIDDEN_SUBPROCESS_OPTIONS,
    });
    return stripAnsi(output.toString());
  } catch (err) {
    const stderr = (err as { stderr?: Buffer }).stderr?.toString() || "";
    return stripAnsi(stderr) || "Doctor check failed.";
  }
}

const OPENCLAW_DIR_NAMES = [".openclaw", ".clawdbot", ".moldbot"];

// qiqiclaw-desktop itself creates ~/.openclaw/claw3d/ as a stub when preparing
// Claw3D settings (see claw3d.ts:writeClaw3dSettings), so a bare `existsSync`
// check would surface that empty stub as a "real" OpenClaw install and
// prompt the user to migrate from themselves. Require at least one regular
// file anywhere in the tree so empty scaffolding doesn't trigger the banner.
function dirContainsAnyFile(dir: string, maxDepth = 3): boolean {
  try {
    const entries = readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile()) return true;
      if (entry.isDirectory() && maxDepth > 0) {
        if (dirContainsAnyFile(join(dir, entry.name), maxDepth - 1)) {
          return true;
        }
      }
    }
  } catch {
    // unreadable → treat as empty
  }
  return false;
}

export function checkOpenClawExists(home: string = homedir()): {
  found: boolean;
  path: string | null;
} {
  for (const name of OPENCLAW_DIR_NAMES) {
    const dir = join(home, name);
    if (existsSync(dir) && dirContainsAnyFile(dir)) {
      return { found: true, path: dir };
    }
  }
  return { found: false, path: null };
}

export async function runClawMigrate(
  onProgress: (progress: InstallProgress) => void,
): Promise<void> {
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) {
    throw new Error("QiQiClaw is not installed.");
  }

  const openclaw = checkOpenClawExists();
  if (!openclaw.found) {
    throw new Error("No OpenClaw installation found.");
  }

  let log = "";
  function emit(text: string): void {
    log += text;
    onProgress({
      step: 1,
      totalSteps: 1,
      title: "Migrating from OpenClaw",
      detail: text.trim().slice(0, 120),
      log,
    });
  }

  emit(`Migrating from ${openclaw.path}...\n`);

  return new Promise((resolve, reject) => {
    const args = qiqiclawCliArgs(["claw", "migrate", "--preset", "full"]);

    const proc = spawn(QIQICLAW_PYTHON, args, {
      cwd: QIQICLAW_REPO,
      env: buildQiqiclawEnv({ TERM: "dumb" }),
      stdio: ["ignore", "pipe", "pipe"],
      ...HIDDEN_SUBPROCESS_OPTIONS,
    });

    proc.stdout?.on("data", (data: Buffer) => {
      emit(stripAnsi(data.toString()));
    });

    proc.stderr?.on("data", (data: Buffer) => {
      emit(stripAnsi(data.toString()));
    });

    proc.on("close", (code) => {
      if (code === 0) {
        emit("\nMigration complete!\n");
        resolve();
      } else {
        reject(new Error(`Migration failed (exit code ${code}).`));
      }
    });

    proc.on("error", (err) => {
      reject(new Error(`Failed to run migration: ${err.message}`));
    });
  });
}

export async function runHermesUpdate(
  onProgress: (progress: InstallProgress) => void,
): Promise<void> {
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) {
    throw new Error("QiQiClaw is not installed. Please install it first.");
  }

  let log = "";
  function emit(text: string): void {
    log += text;
    onProgress({
      step: 1,
      totalSteps: 1,
      title: "Updating QiQiClaw",
      detail: text.trim().slice(0, 120),
      log,
    });
  }

  emit("Running QiQiClaw update...\n");

  return new Promise((resolve, reject) => {
    const proc = spawn(QIQICLAW_PYTHON, qiqiclawCliArgs(["update"]), {
      cwd: QIQICLAW_REPO,
      env: buildQiqiclawEnv({ TERM: "dumb" }),
      stdio: ["ignore", "pipe", "pipe"],
      ...HIDDEN_SUBPROCESS_OPTIONS,
    });

    proc.stdout?.on("data", (data: Buffer) => {
      emit(stripAnsi(data.toString()));
    });

    proc.stderr?.on("data", (data: Buffer) => {
      emit(stripAnsi(data.toString()));
    });

    proc.on("close", (code) => {
      if (code === 0) {
        emit("\nUpdate complete!\n");
        resolve();
      } else {
        reject(new Error(`Update failed (exit code ${code}).`));
      }
    });

    proc.on("error", (err) => {
      reject(new Error(`Failed to run update: ${err.message}`));
    });
  });
}

function getShellProfile(home: string): string | null {
  // Check for the user's shell profile to source their PATH
  const candidates = [
    join(home, ".zshrc"),
    join(home, ".bashrc"),
    join(home, ".bash_profile"),
    join(home, ".profile"),
  ];
  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  return null;
}

// Parse install.sh / install.ps1 output to detect progress stages.
// Patterns are tuned to match both bash and PowerShell installer phrasing.
const STAGE_MARKERS: { pattern: RegExp; step: number; title: string }[] = [
  {
    pattern: /Checking (for )?(git|uv|python|node|ripgrep|ffmpeg)/i,
    step: 1,
    title: "Checking prerequisites",
  },
  {
    pattern: /Installing uv|uv found|uv installed/i,
    step: 2,
    title: "Setting up package manager",
  },
  {
    pattern: /Installing Python|Python .* found|Python installed/i,
    step: 3,
    title: "Setting up Python",
  },
  {
    pattern:
      /Cloning|cloning|Updating.*repository|Repository|Installing to .*qiqiclaw|Downloading PortableGit/i,
    step: 4,
    title: "Downloading QiQiClaw",
  },
  {
    pattern: /Creating virtual|virtual environment|uv venv|\bvenv\b/i,
    step: 5,
    title: "Creating Python environment",
  },
  {
    pattern:
      /pip install|Installing.*packages|dependencies|Trying tier|Resolving|Main package installed/i,
    step: 6,
    title: "Installing dependencies",
  },
  {
    // Only fire step 7 on the install script's actual final lines.
    // Intermediate "Browser engine setup complete" / "All dependencies installed"
    // used to match here and pinned the progress bar at 100% while Playwright
    // and TUI deps were still running — see issue #104.
    pattern:
      /Installation complete|qiqiclaw command ready|Configuration directory ready|QiQiClaw (installation )?(finished|is ready)/i,
    step: 7,
    title: "Finishing setup",
  },
];

export async function runInstall(
  onProgress: (progress: InstallProgress) => void,
  parentWindow?: BrowserWindow | null,
): Promise<void> {
  const totalSteps = 7;
  let log = "";
  let currentStep = 1;
  let currentTitle = "Starting installation...";

  function emit(text: string): void {
    log += text;
    // Try to detect which stage we're in from the output
    for (const marker of STAGE_MARKERS) {
      if (marker.pattern.test(text)) {
        if (marker.step >= currentStep) {
          currentStep = marker.step;
          currentTitle = marker.title;
        }
        break;
      }
    }
    onProgress({
      step: currentStep,
      totalSteps,
      title: currentTitle,
      detail: text.trim().slice(0, 120),
      log,
    });
  }

  emit("Running official QiQiClaw install script...\n");

  if (IS_WINDOWS) {
    return runInstallWindows(emit);
  }

  // Ask for the sudo password ONCE upfront and warm sudo's credential cache
  // before install.sh runs. Playwright's `install --with-deps` later invokes
  // `sudo apt-get` from a subprocess with no TTY — without a warm cache it
  // hangs forever waiting on stdin. See issues #104 and #109.
  emit("→ Checking administrator access...\n");
  const sudoPrecache = await precacheSudoCredentials(parentWindow ?? null);
  if (sudoPrecache.cancelled) {
    throw new Error(
      "Installation cancelled: administrator password is required to install browser libraries.",
    );
  }
  if (!sudoPrecache.ok) {
    emit(
      "⚠ Administrator password was not accepted. Continuing without — install may stall at the browser dependency step.\n",
    );
  } else {
    emit("✓ Administrator access granted\n");
  }

  // Keep the legacy askpass bridge as a fallback for any sudo call that
  // somehow escapes the cred cache (e.g. install runs past sudo's 15min TTL
  // and the keepalive failed).
  let askpass: AskpassHandle | null = null;
  try {
    askpass = await setupAskpass(parentWindow ?? null);
  } catch (err) {
    emit(
      `\n[askpass] Could not set up GUI password bridge: ${(err as Error).message}\n`,
    );
  }

  try {
    return await new Promise<void>((resolve, reject) => {
      const home = homedir();

      // Source the user's shell profile to get the same PATH as their terminal,
      // then run the official install script. Electron apps launched from Finder
      // don't inherit the terminal environment.
      const shellProfile = getShellProfile(home);
      const installCmd = [
        shellProfile ? `source "${shellProfile}" 2>/dev/null;` : "",
        "set -o pipefail; curl -4 -fsSL https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.sh | bash -s -- --skip-setup",
      ].join(" ");

      const basePath = getEnhancedPath();
      const proc = spawn("bash", ["-c", installCmd], {
        cwd: home,
        env: {
          ...process.env,
          PATH: askpass ? `${askpass.pathPrepend}:${basePath}` : basePath,
          HOME: home,
          QIQICLAW_HOME: QIQICLAW_HOME,
          TERM: "dumb",
          ...(askpass?.env ?? {}),
        },
        stdio: ["ignore", "pipe", "pipe"],
        ...HIDDEN_SUBPROCESS_OPTIONS,
      });

      proc.stdout?.on("data", (data: Buffer) => {
        emit(stripAnsi(data.toString()));
      });

      proc.stderr?.on("data", (data: Buffer) => {
        emit(stripAnsi(data.toString()));
      });

      proc.on("close", (code) => {
        if (code === 0) {
          emit("\nInstallation complete!\n");
          resolve();
        } else {
          // The install script can exit non-zero due to benign issues
          // (e.g. git stash pop failure on already-clean repo).
          // If QiQiClaw is actually installed and working, treat as success.
          if (existsSync(QIQICLAW_PYTHON) && hasQiqiclawCli()) {
            emit(
              "\nInstall script exited with warnings, but QiQiClaw is installed successfully.\n",
            );
            resolve();
          } else {
            reject(
              new Error(
                `Installation failed (exit code ${code}). You can try installing via terminal instead.`,
              ),
            );
          }
        }
      });

      proc.on("error", (err) => {
        reject(new Error(`Failed to start installer: ${err.message}`));
      });
    });
  } finally {
    askpass?.cleanup();
    sudoPrecache.stop();
  }
}

// PS single-quoted string escape: ' → ''
function psQuote(s: string): string {
  return `'${s.replace(/'/g, "''")}'`;
}

// Resolve a powershell executable. Prefer PowerShell 7 (`pwsh`) when present,
// fall back to Windows PowerShell 5.1 (`powershell.exe`). Both ship the same
// flags we use; pwsh is faster and writes UTF-8 without a BOM by default.
function resolvePowerShellExe(): string {
  // Spawn will resolve from PATH; we test for pwsh.exe first.
  const programFiles = process.env["ProgramFiles"];
  const candidates = [
    programFiles ? join(programFiles, "PowerShell", "7", "pwsh.exe") : null,
    "pwsh.exe",
    "powershell.exe",
  ].filter((p): p is string => Boolean(p));
  for (const c of candidates) {
    if (c.includes("\\") && existsSync(c)) return c;
  }
  // Let spawn search PATH for the bare names; powershell.exe ships on every
  // supported Windows version, so this is always resolvable.
  return "powershell.exe";
}

async function runInstallWindows(emit: (t: string) => void): Promise<void> {
  // We can't `irm | iex` and pass parameters, and we want to override the
  // installer defaults so the desktop app's QiQiClaw home stays at ~/.qiqiclaw.
  // Strategy: write a small wrapper .ps1 to %TEMP%, run it with -File.
  const home = homedir();
  const qiqiclawHome = QIQICLAW_HOME;
  const installDir = QIQICLAW_REPO;

  const wrapperPath = join(
    tmpdir(),
    `qiqiclaw-install-${randomBytes(6).toString("hex")}.ps1`,
  );

  // The wrapper downloads install.ps1 to a sibling temp file and invokes it
  // with our parameters. This sidesteps the `iex`-can't-pass-args limitation.
  const wrapperScript = [
    "$ErrorActionPreference = 'Stop'",
    // Force TLS 1.2 for older Windows PowerShell 5.1 hosts that still default
    // to TLS 1.0 — github raw refuses TLS < 1.2.
    "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}",
    "$url = 'https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.ps1'",
    `$installer = Join-Path $env:TEMP ("qiqiclaw-install-script-" + [guid]::NewGuid().ToString() + ".ps1")`,
    // Windows PowerShell 5.1 parses BOM-less files as the legacy ANSI codepage,
    // which mangles the non-ASCII glyphs in install.ps1 and produces parse
    // errors (see issue #149). Re-save with a UTF-8 BOM so PS 5.1 reads it as
    // UTF-8. Idempotent if upstream later adds its own BOM or switches to ASCII.
    "$resp = Invoke-WebRequest -Uri $url -UseBasicParsing",
    "$text = if ($resp.Content -is [byte[]]) { [System.Text.Encoding]::UTF8.GetString($resp.Content) } else { [string]$resp.Content }",
    "if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }",
    "[System.IO.File]::WriteAllText($installer, $text, (New-Object System.Text.UTF8Encoding $true))",
    `& $installer -SkipSetup -QiqiclawHome ${psQuote(qiqiclawHome)} -InstallDir ${psQuote(installDir)}`,
    "$exit = $LASTEXITCODE",
    "Remove-Item -Force -ErrorAction SilentlyContinue $installer",
    "exit $exit",
    "",
  ].join("\r\n");

  try {
    writeFileSync(wrapperPath, wrapperScript, { encoding: "utf8" });
  } catch (err) {
    throw new Error(
      `Failed to stage Windows installer: ${(err as Error).message}`,
    );
  }

  const psExe = resolvePowerShellExe();
  const basePath = getEnhancedPath();

  return new Promise<void>((resolve, reject) => {
    const proc = spawn(
      psExe,
      [
        "-ExecutionPolicy",
        "Bypass",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        wrapperPath,
      ],
      {
        cwd: home,
        env: {
          ...process.env,
          PATH: basePath,
          QIQICLAW_HOME: qiqiclawHome,
          // Hint that we're not interactive so install.ps1 doesn't `pause`
          // (the .cmd wrapper does on failure, but -File on .ps1 won't).
          NO_COLOR: "1",
        },
        stdio: ["ignore", "pipe", "pipe"],
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
    );

    proc.stdout?.on("data", (data: Buffer) => {
      emit(stripAnsi(data.toString()));
    });

    proc.stderr?.on("data", (data: Buffer) => {
      emit(stripAnsi(data.toString()));
    });

    proc.on("close", (code) => {
      try {
        unlinkSync(wrapperPath);
      } catch {
        /* best-effort */
      }
      if (code === 0) {
        emit("\nInstallation complete!\n");
        resolve();
        return;
      }
      // Same tolerance as the bash path: if the binary tree exists, count it.
      if (existsSync(QIQICLAW_PYTHON) && hasQiqiclawCli()) {
        emit(
          "\nInstall script exited with warnings, but QiQiClaw is installed successfully.\n",
        );
        resolve();
      } else {
        reject(
          new Error(
            `Installation failed (exit code ${code}). Open PowerShell and try: irm https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.ps1 | iex`,
          ),
        );
      }
    });

    proc.on("error", (err) => {
      try {
        unlinkSync(wrapperPath);
      } catch {
        /* best-effort */
      }
      // Most common failure: PowerShell is missing or blocked by policy.
      const hint =
        (err as NodeJS.ErrnoException).code === "ENOENT"
          ? " PowerShell was not found. Reinstall Windows PowerShell or run the installer manually from a terminal."
          : "";
      reject(new Error(`Failed to start installer: ${err.message}.${hint}`));
    });
  });
}

// ────────────────────────────────────────────────────
//  Backup & Import
// ────────────────────────────────────────────────────

export async function runHermesBackup(
  profile?: string,
): Promise<{ success: boolean; path?: string; error?: string }> {
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) {
    return { success: false, error: "QiQiClaw is not installed." };
  }
  const args = qiqiclawCliArgs();
  if (profile && profile !== "default") args.push("-p", profile);
  args.push("backup");

  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      args,
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv({ TERM: "dumb" }),
        timeout: 120000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error, stdout, stderr) => {
        if (error) {
          resolve({
            success: false,
            error: stripAnsi(stderr || error.message).slice(0, 500),
          });
          return;
        }
        const output = stripAnsi(stdout);
        // Try to extract the backup file path from output
        const pathMatch = output.match(
          /(?:Backup saved|Written|Created).*?(\S+\.(?:tar\.gz|zip|tgz))/i,
        );
        resolve({
          success: true,
          path: pathMatch?.[1] || output.trim().split("\n").pop()?.trim(),
        });
      },
    );
  });
}

export async function runHermesImport(
  archivePath: string,
  profile?: string,
): Promise<{ success: boolean; error?: string }> {
  const archive = validateImportArchivePath(archivePath);
  if (!archive.success) {
    return { success: false, error: archive.error };
  }

  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) {
    return { success: false, error: "QiQiClaw is not installed." };
  }
  const args = qiqiclawCliArgs();
  if (profile && profile !== "default") args.push("-p", profile);
  args.push("import", archive.path);

  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      args,
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv({ TERM: "dumb" }),
        timeout: 120000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error, _stdout, stderr) => {
        if (error) {
          resolve({
            success: false,
            error: stripAnsi(stderr || error.message).slice(0, 500),
          });
          return;
        }
        resolve({ success: true });
      },
    );
  });
}

export function validateImportArchivePath(
  archivePath: unknown,
): { success: true; path: string } | { success: false; error: string } {
  if (typeof archivePath !== "string" || archivePath.trim() === "") {
    return { success: false, error: "Import archive path is required." };
  }

  const path = resolve(archivePath);
  if (!existsSync(path)) {
    return { success: false, error: "Import archive does not exist." };
  }

  try {
    if (!statSync(path).isFile()) {
      return { success: false, error: "Import archive must be a file." };
    }
  } catch {
    return { success: false, error: "Import archive is not readable." };
  }

  return { success: true, path };
}

// ────────────────────────────────────────────────────
//  Debug dump
// ────────────────────────────────────────────────────

export function runHermesDump(): Promise<string> {
  if (!existsSync(QIQICLAW_PYTHON) || !hasQiqiclawCli()) {
    return Promise.resolve("QiQiClaw is not installed.");
  }
  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      qiqiclawCliArgs(["dump"]),
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv({ TERM: "dumb" }),
        timeout: 30000,
        ...HIDDEN_SUBPROCESS_OPTIONS,
      },
      (error, stdout, stderr) => {
        if (error) {
          resolve(stripAnsi(stderr || error.message));
        } else {
          resolve(stripAnsi(stdout));
        }
      },
    );
  });
}

// ────────────────────────────────────────────────────
//  Memory provider discovery
// ────────────────────────────────────────────────────

export interface MemoryProviderInfo {
  name: string;
  description: string;
  installed: boolean;
  active: boolean;
  envVars: string[];
}

/**
 * Discover available memory providers by scanning the plugins directory
 * and reading config.yaml for the active provider.
 */
export function discoverMemoryProviders(
  profile?: string,
): MemoryProviderInfo[] {
  const pluginsDir = join(QIQICLAW_REPO, "plugins", "memory");
  if (!existsSync(pluginsDir)) return [];

  const activeProvider = getActiveMemoryProvider(profile);

  // Known providers with their metadata (from plugin.yaml files)
  const KNOWN_PROVIDERS: Record<
    string,
    { description: string; envVars: string[]; pip?: string }
  > = {
    honcho: {
      description: "memory.providers.honcho",
      envVars: ["HONCHO_API_KEY"],
      pip: "honcho-ai",
    },
    hindsight: {
      description: "memory.providers.hindsight",
      envVars: ["HINDSIGHT_API_KEY", "HINDSIGHT_API_URL", "HINDSIGHT_BANK_ID"],
      pip: "hindsight-client",
    },
    mem0: {
      description: "memory.providers.mem0",
      envVars: ["MEM0_API_KEY"],
      pip: "mem0ai",
    },
    retaindb: {
      description: "memory.providers.retaindb",
      envVars: ["RETAINDB_API_KEY"],
    },
    supermemory: {
      description: "memory.providers.supermemory",
      envVars: ["SUPERMEMORY_API_KEY"],
      pip: "supermemory",
    },
    holographic: {
      description: "memory.providers.holographic",
      envVars: [],
    },
    openviking: {
      description: "memory.providers.openviking",
      envVars: ["OPENVIKING_ENDPOINT", "OPENVIKING_API_KEY"],
    },
    byterover: {
      description: "memory.providers.byterover",
      envVars: ["BRV_API_KEY"],
    },
  };

  const results: MemoryProviderInfo[] = [];

  try {
    const dirs = readdirSync(pluginsDir, { withFileTypes: true });
    for (const d of dirs) {
      if (!d.isDirectory() || d.name.startsWith("_")) continue;
      const name = d.name;
      const known = KNOWN_PROVIDERS[name];
      const initFile = join(pluginsDir, name, "__init__.py");
      const installed = existsSync(initFile);

      results.push({
        name,
        description: known?.description || name,
        installed,
        active: name === activeProvider,
        envVars: known?.envVars || [],
      });
    }
  } catch {
    /* non-fatal */
  }

  // Sort: active first, then installed, then alphabetical
  results.sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1;
    if (a.installed !== b.installed) return a.installed ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return results;
}

/**
 * Read the active memory provider from config.yaml.
 */
export function getActiveMemoryProvider(profile?: string): string {
  try {
    const configPath = join(profileHome(profile), "config.yaml");
    if (!existsSync(configPath)) return "";
    const content = readFileSync(configPath, "utf-8");
    return getYamlPath(content, "memory.provider") || "";
  } catch {
    return "";
  }
}

// ────────────────────────────────────────────────────
//  MCP server management
// ────────────────────────────────────────────────────

export function listMcpServers(
  profile?: string,
): Array<{ name: string; type: string; enabled: boolean; detail: string }> {
  try {
    const configPath = join(profileHome(profile), "config.yaml");
    if (!existsSync(configPath)) return [];
    const content = readFileSync(configPath, "utf-8");
    // Simple YAML parse for mcp_servers section
    const match = content.match(/^mcp_servers:\s*\n((?:[ \t]+.+\n)*)/m);
    if (!match) return [];

    const servers: Array<{
      name: string;
      type: string;
      enabled: boolean;
      detail: string;
    }> = [];
    const block = match[1];
    // Each top-level key under mcp_servers is a server name (2-space indent)
    const nameRe = /^[ ]{2}(\w[\w-]*):\s*$/gm;
    let m: RegExpExecArray | null;
    while ((m = nameRe.exec(block)) !== null) {
      const name = m[1];
      // Extract following indented block for this server.
      // Find the next line at exactly 2-space indent (next server name).
      const start = m.index + m[0].length;
      const nextMatch = /\n {2}\w/g;
      nextMatch.lastIndex = start;
      const next = nextMatch.exec(block);
      const serverBlock = block.slice(start, next ? next.index : undefined);
      const hasUrl = /url:/.test(serverBlock);
      const hasCommand = /command:/.test(serverBlock);
      const enabledMatch = serverBlock.match(/enabled:\s*(true|false)/i);
      const enabled =
        enabledMatch === null || enabledMatch[1].toLowerCase() === "true";

      let detail = "";
      if (hasUrl) {
        const urlMatch = serverBlock.match(/url:\s*["']?([^\s"']+)/);
        detail = urlMatch?.[1] || "HTTP";
      } else if (hasCommand) {
        const cmdMatch = serverBlock.match(/command:\s*["']?([^\s"']+)/);
        detail = cmdMatch?.[1] || "stdio";
      }

      servers.push({
        name,
        type: hasUrl ? "http" : "stdio",
        enabled,
        detail,
      });
    }
    return servers;
  } catch {
    return [];
  }
}

// ────────────────────────────────────────────────────
//  Log viewer
// ────────────────────────────────────────────────────

export function readLogs(
  logFile = "agent.log",
  lines = 200,
): { content: string; path: string } {
  const logsDir = join(QIQICLAW_HOME, "logs");
  // Sanitize: only allow known log file names
  const allowed = ["agent.log", "errors.log", "gateway.log"];
  const file = allowed.includes(logFile) ? logFile : "agent.log";
  const fullPath = join(logsDir, file);

  if (!existsSync(fullPath)) {
    return { content: "", path: fullPath };
  }
  try {
    const content = readFileSync(fullPath, "utf-8");
    // Return the last N lines
    const allLines = content.split("\n");
    const tail = allLines.slice(-lines).join("\n");
    return { content: tail, path: fullPath };
  } catch {
    return { content: "", path: fullPath };
  }
}
