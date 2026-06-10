import { describe, expect, it, vi, beforeEach } from "vitest";

const { execFileSpy } = vi.hoisted(() => ({
  execFileSpy: vi.fn(),
}));

vi.mock("child_process", () => ({
  execFile: execFileSpy,
  default: { execFile: execFileSpy },
}));

vi.mock("../src/main/installer", () => ({
  QIQICLAW_PYTHON: "/tmp/qiqiclaw/venv/bin/python",
  QIQICLAW_REPO: "/tmp/qiqiclaw",
  buildQiqiclawEnv: () => ({ QIQICLAW_HOME: "/tmp/qiqiclaw-home" }),
  qiqiclawCliArgs: (args: string[] = []) => ["qiqiclaw", ...args],
}));

vi.mock("../src/main/qiqiclaw", () => ({
  isRemoteOnlyMode: () => false,
}));

vi.mock("../src/main/config", () => ({
  getConnectionConfig: () => ({ mode: "local" }),
}));

vi.mock("../src/main/ssh-remote", () => ({
  sshRunKanban: vi.fn(),
  sshListClaw3dHqTasks: vi.fn(),
}));

import { currentBoard, listBoards, switchBoard } from "../src/main/kanban";

describe("QiQiClaw single-board kanban adapter", () => {
  beforeEach(() => {
    execFileSpy.mockReset();
  });

  it("synthesizes the default board from `kanban list --json`", async () => {
    execFileSpy.mockImplementation((_bin, _args, _opts, cb) => {
      cb(
        null,
        JSON.stringify([
          { id: "1", title: "A", status: "todo" },
          { id: "2", title: "B", status: "done" },
        ]),
        "",
      );
    });

    const res = await listBoards();

    expect(res.success).toBe(true);
    expect(res.data?.[0]).toMatchObject({
      slug: "default",
      name: "QiQiClaw",
      is_current: true,
      total: 2,
      counts: { todo: 1, done: 1 },
    });
    expect(execFileSpy.mock.calls[0][1]).toEqual([
      "qiqiclaw",
      "kanban",
      "list",
      "--json",
    ]);
  });

  it("does not call unsupported `kanban boards` commands", async () => {
    await expect(currentBoard()).resolves.toEqual({
      success: true,
      data: "default",
    });
    await expect(switchBoard("default")).resolves.toEqual({ success: true });
    await expect(switchBoard("other")).resolves.toMatchObject({
      success: false,
    });
    expect(execFileSpy).not.toHaveBeenCalled();
  });
});
