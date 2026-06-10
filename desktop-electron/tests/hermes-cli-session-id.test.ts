import { EventEmitter } from "events";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";

const {
  spawned,
  TEST_HOME,
  TEST_REPO,
  healthStatuses,
  modelEndpointFailures,
  apiRequests,
  modelConfigRef,
  credentialPoolRef,
  pythonPathRef,
} = vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const path = require("path");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const os = require("os");
  return {
    spawned: [] as Array<
      EventEmitter & {
        stdout: EventEmitter;
        stderr: EventEmitter;
        killed: boolean;
        kill: ReturnType<typeof vi.fn>;
        unref: ReturnType<typeof vi.fn>;
        spawnArgs?: unknown[];
        spawnOptions?: { env?: Record<string, string> };
      }
    >,
    TEST_HOME: path.join(os.tmpdir(), `hermes-cli-session-test-${Date.now()}`),
    TEST_REPO: os.tmpdir(),
    healthStatuses: [] as number[],
    modelEndpointFailures: [] as Array<Error & { code?: string }>,
    apiRequests: [] as Array<{
      body: string;
      headers: Record<string, string>;
    }>,
    modelConfigRef: {
      value: {
        model: "test-model",
        provider: "openrouter",
        baseUrl: "",
      },
    },
    credentialPoolRef: {
      value: {} as Record<string, unknown[]>,
    },
    pythonPathRef: {
      value: process.execPath,
    },
  };
});

vi.mock("http", () => ({
  default: {
    request: (
      _url: string,
      _options: Record<string, unknown>,
      cb?: (res: {
        statusCode: number;
        headers?: Record<string, string>;
        resume?: () => void;
        setEncoding?: (encoding: string) => void;
        on?: (event: string, handler: (...args: unknown[]) => void) => void;
      }) => void,
    ) => {
      let body = "";
      const handlers = new Map<string, (...args: unknown[]) => void>();
      const req = {
        write: (chunk: string | Buffer) => {
          body += chunk.toString();
        },
        end: () => {
          if (_url.endsWith("/health")) {
            cb?.({
              statusCode: healthStatuses.shift() ?? 503,
              resume: () => {},
            });
            return;
          }

          if (_url.endsWith("/v1/models")) {
            const failure = modelEndpointFailures.shift();
            if (failure) {
              handlers.get("error")?.(failure);
              return;
            }

            const res = new EventEmitter() as EventEmitter & {
              statusCode: number;
              headers: Record<string, string>;
              setEncoding: (encoding: string) => void;
            };
            res.statusCode = 200;
            res.headers = {};
            res.setEncoding = () => {};
            cb?.(res);
            queueMicrotask(() => {
              res.emit("data", Buffer.from('{"data":[]}'));
              res.emit("end");
            });
            return;
          }

          if (_url.endsWith("/v1/chat/completions")) {
            apiRequests.push({
              body,
              headers: (_options.headers as Record<string, string>) || {},
            });
            const res = new EventEmitter() as EventEmitter & {
              statusCode: number;
              headers: Record<string, string>;
            };
            res.statusCode = 200;
            res.headers = { "x-hermes-session-id": "desk-cold-gateway" };
            cb?.(res);
            queueMicrotask(() => {
              res.emit(
                "data",
                Buffer.from(
                  'data: {"choices":[{"delta":{"content":"Hi from API"}}]}\n\n',
                ),
              );
              res.emit("data", Buffer.from("data: [DONE]\n\n"));
              res.emit("end");
            });
          }
        },
        on: (event: string, handler: (...args: unknown[]) => void) => {
          handlers.set(event, handler);
          return req;
        },
        destroy: () => {
          handlers.get("error")?.(new Error("destroyed"));
        },
      };
      return req;
    },
  },
}));

vi.mock("https", () => ({
  default: {
    request: () => ({
      write: () => {},
      end: () => {},
      on: () => {},
      destroy: () => {},
    }),
  },
}));

vi.mock("child_process", () => ({
  default: {
    spawn: vi.fn((...args: unknown[]) => {
      const proc = Object.assign(new EventEmitter(), {
        stdout: new EventEmitter(),
        stderr: new EventEmitter(),
        killed: false,
        kill: vi.fn(),
        unref: vi.fn(),
        spawnArgs: args,
        spawnOptions: args[2] as { env?: Record<string, string> } | undefined,
      });
      spawned.push(proc);
      return proc;
    }),
  },
  spawn: vi.fn((...args: unknown[]) => {
    const proc = Object.assign(new EventEmitter(), {
      stdout: new EventEmitter(),
      stderr: new EventEmitter(),
      killed: false,
      kill: vi.fn(),
      unref: vi.fn(),
      spawnArgs: args,
      spawnOptions: args[2] as { env?: Record<string, string> } | undefined,
    });
    spawned.push(proc);
    return proc;
  }),
}));

vi.mock("../src/main/installer", () => ({
  QIQICLAW_HOME: TEST_HOME,
  get QIQICLAW_PYTHON() {
    return pythonPathRef.value;
  },
  QIQICLAW_REPO: TEST_REPO,
  buildQiqiclawEnv: (extra: Record<string, string> = {}) => ({
    ...process.env,
    PATH: process.env.PATH || "",
    HOME: TEST_HOME,
    QIQICLAW_HOME: TEST_HOME,
    ...extra,
  }),
  qiqiclawCliArgs: (extra?: string[]) => ["/dev/null", ...(extra || [])],
  getEnhancedPath: () => process.env.PATH || "",
}));

vi.mock("../src/main/config", () => ({
  getModelConfig: () => modelConfigRef.value,
  readEnv: () => ({}),
  getCredentialPool: () => credentialPoolRef.value,
  getApiServerKey: () => "",
  getConnectionConfig: () => ({ mode: "local" as const }),
}));

vi.mock("../src/main/ssh-tunnel", () => ({
  getSshTunnelUrl: () => null,
  isSshTunnelActive: () => false,
  isSshTunnelHealthy: () => Promise.resolve(false),
  startSshTunnel: () => Promise.resolve(),
}));

vi.mock("../src/main/utils", () => ({
  stripAnsi: (s: string) => s,
  pidIsAliveAs: () => false,
  getActiveProfileNameSync: () => "default",
}));

vi.mock("../src/main/models", () => ({
  readModels: () => [],
}));

vi.mock("../src/main/process-options", () => ({
  HIDDEN_SUBPROCESS_OPTIONS: {},
}));

import {
  ensureLocalApiUsable,
  sendMessage,
  startGateway,
  stopGateway,
  stopHealthPolling,
} from "../src/main/hermes";

describe("CLI fallback session id propagation", () => {
  beforeEach(() => {
    healthStatuses.length = 0;
    modelEndpointFailures.length = 0;
    apiRequests.length = 0;
    modelConfigRef.value = {
      model: "test-model",
      provider: "openrouter",
      baseUrl: "",
    };
    credentialPoolRef.value = {};
    pythonPathRef.value = process.execPath;
  });

  afterEach(() => {
    stopGateway(true);
    stopHealthPolling();
    spawned.length = 0;
  });

  it("captures the quiet CLI session id from stderr so the next desktop turn can resume it", async () => {
    const done = new Promise<string | undefined>((resolve) => {
      sendMessage("hi", {
        onChunk: () => {},
        onDone: resolve,
        onError: () => {},
      }).then(() => {
        const proc = spawned[0];
        proc.stdout.emit("data", Buffer.from("Hi there"));
        proc.stderr.emit(
          "data",
          Buffer.from("\nsession_id: 20260527_143413_10df4c\n"),
        );
        proc.emit("close", 0);
      });
    });

    await expect(done).resolves.toBe("20260527_143413_10df4c");
  });

  it("normalises relay root base URL and resolves the chat key from credential pool for CLI fallback", async () => {
    modelConfigRef.value = {
      model: "gpt-5.5",
      provider: "custom",
      baseUrl: "https://oneapi.hk",
    };
    credentialPoolRef.value = {
      "custom:oneapi": [
        {
          access_token: "sk-relay-from-pool",
          base_url: "https://oneapi.hk",
        },
      ],
    };

    const done = new Promise<string | undefined>((resolve) => {
      sendMessage("hi", {
        onChunk: () => {},
        onDone: resolve,
        onError: () => {},
      }).then(() => {
        const proc = spawned[0];
        proc.stdout.emit("data", Buffer.from("Hi there"));
        proc.emit("close", 0);
      });
    });

    await expect(done).resolves.toBeUndefined();
    const chatSpawn = spawned.find(
      (proc) => proc.spawnOptions?.env?.OPENAI_BASE_URL,
    );
    expect(chatSpawn).toBeDefined();
    const env = chatSpawn?.spawnOptions?.env || {};
    expect(env.QIQICLAW_INFERENCE_PROVIDER).toBe("custom");
    expect(env.OPENAI_BASE_URL).toBe("https://oneapi.hk/v1");
    expect(env.OPENAI_API_KEY).toBe("sk-relay-from-pool");
  });

  it("treats credential-pool custom provider ids as OpenAI-compatible in CLI fallback", async () => {
    modelConfigRef.value = {
      model: "gpt-5.5",
      provider: "custom:oneapi",
      baseUrl: "https://oneapi.hk",
    };
    credentialPoolRef.value = {
      "custom:oneapi": [
        {
          access_token: "sk-relay-from-pool",
          base_url: "https://oneapi.hk/v1",
        },
      ],
    };

    const done = new Promise<string | undefined>((resolve) => {
      sendMessage("hi", {
        onChunk: () => {},
        onDone: resolve,
        onError: () => {},
      }).then(() => {
        const proc = spawned[0];
        proc.stdout.emit("data", Buffer.from("Hi there"));
        proc.emit("close", 0);
      });
    });

    await expect(done).resolves.toBeUndefined();
    const env = spawned.find((proc) => proc.spawnOptions?.env?.OPENAI_BASE_URL)
      ?.spawnOptions?.env;
    expect(env?.QIQICLAW_INFERENCE_PROVIDER).toBe("custom");
    expect(env?.OPENAI_BASE_URL).toBe("https://oneapi.hk/v1");
    expect(env?.OPENAI_API_KEY).toBe("sk-relay-from-pool");
  });

  it("continues a CLI-created timestamp session over the API instead of minting a desk id", async () => {
    const cliSessionId = "20260527_143413_10df4c";
    const firstDone = new Promise<string | undefined>((resolve) => {
      sendMessage("hi", {
        onChunk: () => {},
        onDone: resolve,
        onError: () => {},
      }).then(() => {
        const proc = spawned[0];
        proc.stdout.emit("data", Buffer.from("Hi there"));
        proc.stderr.emit("data", Buffer.from(`\nsession_id: ${cliSessionId}\n`));
        proc.emit("close", 0);
      });
    });

    await expect(firstDone).resolves.toBe(cliSessionId);

    healthStatuses.push(200);
    await expect(
      new Promise<string | undefined>((resolve, reject) => {
        sendMessage(
          "what time is it?",
          {
            onChunk: () => {},
            onDone: resolve,
            onError: reject,
          },
          undefined,
          cliSessionId,
        ).catch(reject);
      }),
    ).resolves.toBe("desk-cold-gateway");

    expect(apiRequests).toHaveLength(1);
    expect(apiRequests[0].headers["X-QiQiClaw-Session-Id"]).toBe(cliSessionId);
    expect(JSON.parse(apiRequests[0].body)).toMatchObject({
      session_id: cliSessionId,
      messages: [{ role: "user", content: "what time is it?" }],
      stream: true,
    });
  });

  it("waits for a cold gateway to become API-ready instead of falling back to CLI", async () => {
    healthStatuses.push(503, 200);

    expect(startGateway()).toBe(true);
    expect(spawned).toHaveLength(1);

    const chunks: string[] = [];
    const done = new Promise<string | undefined>((resolve, reject) => {
      sendMessage("hi", {
        onChunk: (chunk) => chunks.push(chunk),
        onDone: resolve,
        onError: reject,
      }).catch(reject);
    });

    await expect(done).resolves.toBe("desk-cold-gateway");
    expect(chunks.join("")).toBe("Hi from API");
    expect(spawned).toHaveLength(1);
    expect(apiRequests).toHaveLength(1);
    expect(JSON.parse(apiRequests[0].body)).toMatchObject({
      messages: [{ role: "user", content: "hi" }],
      stream: true,
    });
  });

  it("coalesces concurrent cold gateway readiness checks into a single spawn", async () => {
    healthStatuses.push(503, 200, 200);

    await expect(
      Promise.all([ensureLocalApiUsable(), ensureLocalApiUsable()]),
    ).resolves.toEqual([{ ok: true }, { ok: true }]);
    expect(spawned).toHaveLength(1);
    expect(apiRequests).toHaveLength(0);
  });

  it("returns install diagnostics immediately when the local backend is missing", async () => {
    healthStatuses.push(503);
    pythonPathRef.value = "/definitely/missing/python";

    const result = await ensureLocalApiUsable();

    expect(result.ok).toBe(false);
    expect(result.error).toContain("QiQiClaw local backend is not installed");
    expect(result.error).toContain("Python missing: /definitely/missing/python");
    expect(spawned).toHaveLength(0);
  });

  it("recovers when the models probe sees a refused local gateway connection", async () => {
    const refused = Object.assign(
      new Error("connect ECONNREFUSED 127.0.0.1:8642"),
      { code: "ECONNREFUSED" },
    );
    modelEndpointFailures.push(refused);
    healthStatuses.push(503, 200, 200);

    const result = await ensureLocalApiUsable();

    expect(result).toEqual({ ok: true });
    expect(spawned).toHaveLength(1);
    expect(modelEndpointFailures).toHaveLength(0);
  });

  it("re-checks health when a previously-ready local gateway is restarted cold", async () => {
    healthStatuses.push(200);

    await expect(
      new Promise<string | undefined>((resolve, reject) => {
        sendMessage("warmup", {
          onChunk: () => {},
          onDone: resolve,
          onError: reject,
        }).catch(reject);
      }),
    ).resolves.toBe("desk-cold-gateway");
    expect(apiRequests).toHaveLength(1);

    expect(startGateway()).toBe(true);
    expect(spawned).toHaveLength(1);
    healthStatuses.push(503, 200);

    const chunks: string[] = [];
    await expect(
      new Promise<string | undefined>((resolve, reject) => {
        sendMessage("hi after restart", {
          onChunk: (chunk) => chunks.push(chunk),
          onDone: resolve,
          onError: reject,
        }).catch(reject);
      }),
    ).resolves.toBe("desk-cold-gateway");

    expect(chunks.join("")).toBe("Hi from API");
    expect(spawned).toHaveLength(1);
    expect(apiRequests).toHaveLength(2);
    expect(JSON.parse(apiRequests[1].body)).toMatchObject({
      messages: [{ role: "user", content: "hi after restart" }],
      stream: true,
    });
  });
});
