import { execFile, spawn, type ChildProcess } from "child_process";
import {
  QIQICLAW_PYTHON,
  QIQICLAW_REPO,
  qiqiclawCliArgs,
  buildQiqiclawEnv,
} from "./installer";
import { HIDDEN_SUBPROCESS_OPTIONS } from "./process-options";
import { stripAnsi } from "./utils";

/**
 * Provider identifiers that authenticate via an interactive OAuth flow
 * (`qiqiclaw auth add <provider> --type oauth`) rather than a static API
 * key. Mirrors QiQiClaw's OAuth-capable provider set.
 *
 * `nous` is included even though it also has an API-key variant — the
 * Providers UI now offers both surfaces (an API Key card and an
 * OAuth Sign-in card, issue #367), and the OAuth path goes through
 * this gate. The desktop previously excluded `nous` here on the
 * (incorrect) assumption that it used the normal key flow only.
 */
export const OAUTH_LOGIN_PROVIDERS = [
  "openai-codex",
  "qwen-oauth",
  "google-gemini-cli",
  "minimax-oauth",
  "nous",
] as const;

export type OAuthLoginProvider = (typeof OAUTH_LOGIN_PROVIDERS)[number];

export function isOAuthLoginProvider(
  value: string,
): value is OAuthLoginProvider {
  return (OAUTH_LOGIN_PROVIDERS as readonly string[]).includes(value);
}

export interface OAuthLoginResult {
  success: boolean;
  error?: string;
}

export interface ProviderAuthStatus {
  logged_in: boolean;
  provider?: string;
  auth_store?: string;
  source?: string;
  error?: string;
  error_code?: string;
}

const AUTH_STATUS_SNIPPET =
  "import json,sys; from qiqiclaw_cli.auth import get_provider_auth_status; " +
  "s=get_provider_auth_status(sys.argv[1]); " +
  "s.pop('api_key', None); s.pop('access_token', None); s.pop('refresh_token', None); " +
  "print(json.dumps(s))";

export function getProviderAuthStatus(
  provider: string,
): Promise<ProviderAuthStatus> {
  return new Promise((resolve) => {
    execFile(
      QIQICLAW_PYTHON,
      ["-c", AUTH_STATUS_SNIPPET, provider],
      {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv({ TERM: "dumb" }),
        timeout: 15_000,
        windowsHide: true,
      },
      (err, stdout) => {
        if (err) {
          resolve({
            logged_in: false,
            provider,
            error: err.message,
          });
          return;
        }
        try {
          const parsed = JSON.parse(String(stdout).trim());
          resolve({
            logged_in: !!parsed.logged_in,
            provider: parsed.provider || provider,
            auth_store: parsed.auth_store,
            source: parsed.source,
            error: parsed.error,
            error_code: parsed.error_code,
          });
        } catch (parseErr) {
          resolve({
            logged_in: false,
            provider,
            error: (parseErr as Error).message,
          });
        }
      },
    );
  });
}

/**
 * Parse a device-code login prompt out of the CLI's streamed output.
 * The OpenAI Codex flow — unlike the browser-loopback providers —
 * prints a URL to open and a short code to enter rather than opening a
 * browser itself. Detecting both lets the desktop open the page and
 * pre-copy the code so the user only has to paste.
 *
 * Returns null until both parts are present. Only an `https:` URL is
 * accepted (the value is fed to `shell.openExternal`).
 */
export function detectDeviceCode(
  text: string,
): { url: string; code: string } | null {
  // `[^\S\n]*` is horizontal-whitespace-only — using `\s*` here would
  // silently consume a blank line between the label and the value, making
  // a false-positive match against the wrong line possible.
  const urlMatch = text.match(
    /Open this URL in your browser:[^\S\n]*\n[^\S\n]*(https:\/\/\S+)/,
  );
  const codeMatch = text.match(/Enter this code:[^\S\n]*\n[^\S\n]*(\S+)/);
  if (urlMatch && codeMatch) {
    return { url: urlMatch[1], code: codeMatch[1] };
  }
  return null;
}

// Only one interactive login can run at a time — the renderer surfaces a
// single modal. Tracked so the renderer can cancel a flow the user
// abandoned (otherwise the CLI's loopback OAuth server lingers).
let activeProc: ChildProcess | null = null;

/**
 * Run `qiqiclaw auth add <provider> --type oauth`, streaming the CLI's
 * stdout/stderr line-by-line to `emit`. The CLI opens the system browser
 * for the OAuth consent step and runs a localhost loopback server to
 * catch the redirect; this function just supervises that subprocess.
 *
 * Resolves `{ success: true }` on exit code 0, `{ success: false, error }`
 * otherwise (non-zero exit, spawn failure, or cancellation).
 */
export function runQiqiclawAuthLogin(
  provider: string,
  emit: (chunk: string) => void,
  profile?: string,
): Promise<OAuthLoginResult> {
  return new Promise((resolve) => {
    if (!isOAuthLoginProvider(provider)) {
      resolve({
        success: false,
        error: `Unsupported OAuth provider: ${provider}`,
      });
      return;
    }
    if (activeProc) {
      resolve({
        success: false,
        error: "Another sign-in is already in progress.",
      });
      return;
    }

    // `--type oauth` is explicit so the CLI never falls back to an
    // interactive "API key or OAuth?" prompt on a stdin we've closed.
    const subArgs =
      profile && profile !== "default"
        ? ["-p", profile, "auth", "add", provider, "--type", "oauth"]
        : ["auth", "add", provider, "--type", "oauth"];

    let proc: ChildProcess;
    try {
      proc = spawn(QIQICLAW_PYTHON, qiqiclawCliArgs(subArgs), {
        cwd: QIQICLAW_REPO,
        env: buildQiqiclawEnv({ TERM: "dumb" }),
        stdio: ["pipe", "pipe", "pipe"],
        ...HIDDEN_SUBPROCESS_OPTIONS,
      });
      if (provider === "openai-codex") {
        proc.stdin?.write("y\n");
        proc.stdin?.end();
      } else {
        proc.stdin?.end();
      }
    } catch (err) {
      resolve({ success: false, error: (err as Error).message });
      return;
    }

    activeProc = proc;
    let settled = false;
    let output = "";
    const finish = (result: OAuthLoginResult): void => {
      if (settled) return;
      settled = true;
      activeProc = null;
      resolve(result);
    };

    const emitChunk = (data: Buffer): void => {
      const chunk = stripAnsi(data.toString());
      output += chunk;
      emit(chunk);
    };

    proc.stdout?.on("data", emitChunk);
    proc.stderr?.on("data", emitChunk);

    proc.on("error", (err) => {
      finish({
        success: false,
        error: `Failed to start sign-in: ${err.message}`,
      });
    });

    proc.on("close", (code, signal) => {
      if (code === 0) {
        finish({ success: true });
      } else if (signal) {
        finish({ success: false, error: "Sign-in cancelled." });
      } else {
        const details = output.trim();
        finish({
          success: false,
          error: details || `Sign-in exited with code ${code}.`,
        });
      }
    });
  });
}

/**
 * Kill the in-flight login subprocess, if any. Used when the user closes
 * the sign-in modal before the OAuth flow completes.
 */
export function cancelQiqiclawAuthLogin(): boolean {
  if (!activeProc) return false;
  activeProc.kill();
  return true;
}
