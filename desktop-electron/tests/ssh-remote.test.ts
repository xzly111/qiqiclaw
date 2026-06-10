import { execFileSync } from "child_process";
import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { describe, expect, it, vi } from "vitest";

vi.mock("../src/main/locale", () => ({
  getAppLocale: () => "en",
}));

import {
  buildRemoteQiqiclawCmd,
  sshSetConfigValue,
  buildGatewayStartCommand,
  buildGatewayStopCommand,
  buildGatewayStatusCommand,
} from "../src/main/ssh-remote";
import type { SshConfig } from "../src/main/ssh-tunnel";

/** The `then` clause of the leading `if` — the systemd-managed branch. */
function systemdBranch(command: string): string {
  return command.slice(command.indexOf("then"), command.indexOf("else"));
}

const sshConfig: SshConfig = {
  host: "example.test",
  port: 22,
  username: "qiqiclaw",
  keyPath: "",
  remotePort: 8642,
  localPort: 18642,
};

function runWithQiqiclawShim(command: string): Buffer {
  const home = mkdtempSync(join(tmpdir(), "qiqiclaw-ssh-cmd-home-"));
  const bin = join(home, "bin");
  mkdirSync(bin, { recursive: true });
  const qiqiclaw = join(bin, "qiqiclaw");
  const shim = [
    "#!/usr/bin/env bash",
    'if [ "$1" = "doctor" ]; then',
    '  printf "doctor stderr preserved\\n" >&2',
    "  exit 0",
    "fi",
    'printf "%s\\0" "$@"',
    "",
  ].join("\n");
  writeFileSync(qiqiclaw, shim);
  chmodSync(qiqiclaw, 0o755);
  return execFileSync("bash", ["-lc", command], {
    env: {
      ...process.env,
      HOME: home,
      PATH: `${bin}:${process.env.PATH || ""}`,
    },
  });
}

function parseNulArgs(output: Buffer): string[] {
  const parts = output.toString("utf8").split("\0");
  if (parts.at(-1) === "") parts.pop();
  return parts;
}

describe("ssh remote config writes", () => {
  it.each([
    ["quote", 'bad"value'],
    ["backslash", "bad\\value"],
    ["newline", "bad\nvalue"],
    ["carriage return", "bad\rvalue"],
  ])(
    "rejects YAML-breaking %s values before remote writes",
    async (_name, value) => {
      await expect(
        sshSetConfigValue(sshConfig, "base_url", value),
      ).rejects.toThrow("Config value contains illegal characters");
    },
  );
});

describe("ssh QiQiClaw command quoting", () => {
  it("shell-quotes the whole bash script without dropping per-argument quoting", () => {
    const command = buildRemoteQiqiclawCmd([
      "kanban",
      "create",
      "My task title",
      "--triage",
      "--json",
    ]);

    expect(command).not.toContain(
      "bash -c '[ -x $HOME/qiqiclaw/.venv/bin/hermes ] && exec $HOME/qiqiclaw/.venv/bin/hermes 'kanban' 'create'",
    );
    expect(command).toContain(
      `bash -c '[ -x $HOME/qiqiclaw/.venv/bin/qiqiclaw ] && exec $HOME/qiqiclaw/.venv/bin/qiqiclaw '"'"'kanban'"'"'`,
    );
    expect(command).not.toContain("bin/hermes");
  });

  it.each([
    [
      "multi-word title",
      ["kanban", "create", "My task title", "--triage", "--json"],
    ],
    [
      "multiline markdown body",
      [
        "kanban",
        "create",
        "My task title",
        "--body",
        "first line\n- bullet one\n- bullet two",
        "--triage",
        "--json",
      ],
    ],
    [
      "single quote in user input",
      ["kanban", "create", "User's task", "--json"],
    ],
  ])("preserves %s", (_name, expectedArgs) => {
    const command = buildRemoteQiqiclawCmd(expectedArgs);
    expect(parseNulArgs(runWithQiqiclawShim(command))).toEqual(expectedArgs);
  });

  it("preserves existing extraShell redirects", () => {
    const output = runWithQiqiclawShim(
      buildRemoteQiqiclawCmd(["doctor"], " 2>&1"),
    ).toString("utf8");
    expect(output).toBe("doctor stderr preserved\n");
  });
});

describe("ssh gateway commands (issue #285)", () => {
  it("detects the qiqiclaw systemd gateway unit before acting", () => {
    for (const cmd of [
      buildGatewayStartCommand(),
      buildGatewayStopCommand(),
      buildGatewayStatusCommand(),
    ]) {
      expect(cmd).toContain(
        "systemctl list-unit-files qiqiclaw-gateway.service",
      );
      expect(cmd).not.toContain("systemctl list-unit-files hermes.service");
      expect(cmd.indexOf("if ")).toBeLessThan(cmd.indexOf("else"));
    }
  });

  it("start prefers systemd, falling back to nohup only without a unit", () => {
    const cmd = buildGatewayStartCommand();
    expect(cmd).toContain("systemctl start qiqiclaw-gateway.service");
    expect(cmd).toContain("sudo -n systemctl start qiqiclaw-gateway.service");
    expect(cmd).not.toContain("systemctl start hermes.service");
    expect(cmd).not.toContain("sudo -n systemctl start hermes.service");
    // The nohup fallback must live in the else branch — never alongside
    // systemd, where it would strand the unit in a restart crash-loop.
    expect(cmd).toContain("nohup bash -c");
    expect(cmd).toContain("gateway");
    expect(cmd).toContain("start");
    expect(systemdBranch(cmd)).not.toContain("nohup");
  });

  it("stop routes through systemd, else QiQiClaw CLI gateway stop", () => {
    const cmd = buildGatewayStopCommand();
    expect(cmd).toContain("systemctl stop qiqiclaw-gateway.service");
    expect(cmd).not.toContain("systemctl stop hermes.service");
    expect(cmd).toContain("gateway");
    expect(cmd).toContain("stop");
    expect(systemdBranch(cmd)).not.toContain("gateway stop");
    expect(systemdBranch(cmd)).not.toContain("kill");
  });

  it("status reports the systemd unit state when managed", () => {
    const cmd = buildGatewayStatusCommand();
    expect(cmd).toContain("systemctl is-active qiqiclaw-gateway.service");
    expect(cmd).not.toContain("systemctl is-active hermes.service");
    expect(cmd).toContain("gateway.pid");
    expect(systemdBranch(cmd)).not.toContain("gateway.pid");
  });
});

describe("buildRemoteQiqiclawCmd venv probe (issue #284)", () => {
  const cmd = buildRemoteQiqiclawCmd(["--version"]);

  it("probes both .venv and venv for every install base", () => {
    for (const base of [
      "$HOME/qiqiclaw",
      "$HOME/.qiqiclaw/qiqiclaw",
      "/opt/qiqiclaw/qiqiclaw",
    ]) {
      expect(cmd).toContain(`${base}/.venv/bin/qiqiclaw`);
      expect(cmd).toContain(`${base}/venv/bin/qiqiclaw`);
    }
  });

  it("probes ~/.local/bin where pip --user installs a wrapper", () => {
    expect(cmd).toContain("$HOME/.local/bin/qiqiclaw");
  });

  it("does not probe the /usr/local/bin sudo-wrapper it deliberately bypasses", () => {
    expect(cmd).not.toContain("/usr/local/bin/qiqiclaw");
  });

  it("falls back to bare qiqiclaw on PATH", () => {
    expect(cmd).toContain("command -v qiqiclaw");
    expect(cmd).not.toContain("command -v hermes");
    expect(cmd).not.toContain("exec hermes");
  });
});
