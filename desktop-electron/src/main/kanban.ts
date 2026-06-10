import { execFile, ExecFileOptions } from "child_process";
import {
  QIQICLAW_PYTHON,
  QIQICLAW_REPO,
  buildQiqiclawEnv,
  qiqiclawCliArgs,
} from "./installer";
import { isRemoteOnlyMode } from "./qiqiclaw";
import { getConnectionConfig } from "./config";
import { sshRunKanban, sshListClaw3dHqTasks } from "./ssh-remote";

export interface KanbanTask {
  id: string;
  title: string;
  body: string | null;
  assignee: string | null;
  status: string;
  priority: number;
  tenant: string | null;
  workspace_kind: string;
  workspace_path: string | null;
  created_by: string | null;
  created_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  result: string | null;
  skills: string[];
  max_retries: number | null;
}

export interface KanbanBoard {
  slug: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  is_current: boolean;
  archived?: boolean;
  total: number;
  counts: Record<string, number>;
  db_path?: string;
}

export interface KanbanRun {
  id: number;
  task_id: string;
  profile: string | null;
  status: string | null;
  outcome: string | null;
  summary: string | null;
  error: string | null;
  started_at: number | null;
  ended_at: number | null;
  last_heartbeat_at: number | null;
}

export interface KanbanComment {
  id: number;
  task_id: string;
  author: string | null;
  body: string;
  created_at: number;
}

export interface KanbanEvent {
  id: number;
  task_id: string;
  kind: string;
  payload: Record<string, unknown> | null;
  created_at: number;
  run_id: number | null;
}

export interface KanbanTaskDetail {
  task: KanbanTask;
  comments: KanbanComment[];
  events: KanbanEvent[];
  parents: string[];
  children: string[];
  runs: KanbanRun[];
  latest_summary: string | null;
}

export interface KanbanResult<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  stdout?: string;
  /**
   * Set only when the failure is the connection mode genuinely not
   * supporting Kanban (plain remote HTTP). The renderer keys its
   * "switch modes" screen off this flag — never off the error text —
   * so a real SSH-Kanban failure surfaces its actual error instead of
   * being mislabelled as a mode problem (issue #319).
   */
  unsupportedMode?: boolean;
}

const KANBAN_TIMEOUT_MS = 20000;

interface RunOpts {
  profile?: string;
  parseJson?: boolean;
  timeoutMs?: number;
}

async function runKanban(
  args: string[],
  opts: RunOpts = {},
): Promise<KanbanResult<unknown>> {
  // SSH tunnel mode: dispatch to the remote QiQiClaw CLI over SSH.
  const conn = getConnectionConfig();
  if (conn.mode === "ssh" && conn.ssh) {
    return sshRunKanban(conn.ssh, args, {
      profile: opts.profile,
      parseJson: opts.parseJson,
      timeoutMs: opts.timeoutMs,
    });
  }

  const cliArgs = qiqiclawCliArgs();
  if (opts.profile && opts.profile !== "default") {
    cliArgs.push("-p", opts.profile);
  }
  cliArgs.push("kanban", ...args);

  const execOpts: ExecFileOptions = {
    cwd: QIQICLAW_REPO,
    timeout: opts.timeoutMs ?? KANBAN_TIMEOUT_MS,
    env: buildQiqiclawEnv(),
    maxBuffer: 16 * 1024 * 1024,
  };

  return new Promise((resolve) => {
    execFile(QIQICLAW_PYTHON, cliArgs, execOpts, (err, stdout, stderr) => {
      const out = (stdout || "").toString();
      if (err) {
        resolve({
          success: false,
          error: (stderr || err.message || "").toString().trim(),
          stdout: out,
        });
        return;
      }
      if (opts.parseJson) {
        try {
          resolve({ success: true, data: JSON.parse(out), stdout: out });
        } catch (parseErr) {
          resolve({
            success: false,
            error: `Failed to parse JSON from 'qiqiclaw kanban': ${(parseErr as Error).message}`,
            stdout: out,
          });
        }
        return;
      }
      resolve({ success: true, stdout: out });
    });
  });
}

export function unsupportedInRemote<T>(): KanbanResult<T> {
  return {
    success: false,
    unsupportedMode: true,
    error:
      "Kanban requires either a local QiQiClaw install or SSH tunnel mode. " +
      "Plain remote (HTTP+API key) mode does not yet expose the kanban API. " +
      "Switch to SSH tunnel mode in Settings to use the board against a remote QiQiClaw.",
  };
}

export async function listBoards(
  includeArchived = false,
  profile?: string,
): Promise<KanbanResult<KanbanBoard[]>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const res = await listTasks({ includeArchived, profile });
  if (!res.success) return { success: false, error: res.error };
  const tasks = res.data || [];
  const counts: Record<string, number> = {};
  for (const task of tasks) {
    counts[task.status] = (counts[task.status] || 0) + 1;
  }
  return {
    success: true,
    data: [
      {
        slug: "default",
        name: "QiQiClaw",
        description: "Default QiQiClaw kanban board",
        is_current: true,
        archived: false,
        total: tasks.length,
        counts,
      },
    ],
  };
}

export async function currentBoard(
  profile?: string,
): Promise<KanbanResult<string>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  void profile;
  return { success: true, data: "default" };
}

export async function switchBoard(
  slug: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  if (!slug) return { success: false, error: "Missing board slug" };
  void profile;
  if (slug === "default") return { success: true };
  return {
    success: false,
    error: "QiQiClaw CLI currently supports the default kanban board only.",
  };
}

export async function createBoard(
  slug: string,
  name?: string,
  switchAfter = false,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  if (!slug) return { success: false, error: "Missing board slug" };
  void name;
  void switchAfter;
  void profile;
  if (slug === "default") return { success: true };
  return {
    success: false,
    error: "QiQiClaw CLI currently supports the default kanban board only.",
  };
}

export async function removeBoard(
  slug: string,
  hardDelete = false,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  if (!slug) return { success: false, error: "Missing board slug" };
  void hardDelete;
  void profile;
  return {
    success: false,
    error: "The default QiQiClaw kanban board cannot be removed.",
  };
}

export async function listTasks(
  opts: {
    status?: string;
    assignee?: string;
    tenant?: string;
    includeArchived?: boolean;
    profile?: string;
  } = {},
): Promise<KanbanResult<KanbanTask[]>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const args = ["list", "--json"];
  if (opts.status) args.push("--status", opts.status);
  if (opts.assignee) args.push("--assignee", opts.assignee);
  if (opts.tenant) args.push("--tenant", opts.tenant);
  if (opts.includeArchived) args.push("--archived");
  const res = await runKanban(args, { profile: opts.profile, parseJson: true });
  if (!res.success) return { success: false, error: res.error };
  return { success: true, data: res.data as KanbanTask[] };
}

export async function getTask(
  taskId: string,
  profile?: string,
): Promise<KanbanResult<KanbanTaskDetail>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  if (!taskId) return { success: false, error: "Missing task ID" };
  const res = await runKanban(["show", taskId, "--json"], {
    profile,
    parseJson: true,
  });
  if (!res.success) return { success: false, error: res.error };
  return { success: true, data: res.data as KanbanTaskDetail };
}

export interface CreateTaskInput {
  title: string;
  body?: string;
  assignee?: string;
  priority?: number;
  tenant?: string;
  workspace?: string; // "scratch" | "worktree" | "dir:<path>"
  triage?: boolean;
  skills?: string[];
  maxRetries?: number;
}

export async function createTask(
  input: CreateTaskInput,
  profile?: string,
): Promise<KanbanResult<{ id: string }>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  if (!input.title?.trim()) {
    return { success: false, error: "Title is required" };
  }
  const args = ["create", input.title];
  if (input.body) args.push("--body", input.body);
  if (input.assignee) args.push("--assignee", input.assignee);
  if (input.priority !== undefined)
    args.push("--priority", String(input.priority));
  if (input.tenant) args.push("--tenant", input.tenant);
  if (input.workspace) args.push("--workspace", input.workspace);
  if (input.triage) args.push("--triage");
  if (input.maxRetries !== undefined)
    args.push("--max-retries", String(input.maxRetries));
  for (const skill of input.skills || []) {
    args.push("--skill", skill);
  }
  args.push("--json");

  const res = await runKanban(args, { profile, parseJson: true });
  if (!res.success) return { success: false, error: res.error };
  const data = res.data as { id?: string };
  return { success: true, data: { id: data?.id || "" } };
}

export async function assignTask(
  taskId: string,
  assignee: string | null,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const res = await runKanban(["assign", taskId, assignee || "none"], {
    profile,
  });
  return { success: res.success, error: res.error };
}

export async function completeTask(
  taskId: string,
  result?: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const args = ["complete", taskId];
  if (result) args.push("--result", result);
  const res = await runKanban(args, { profile });
  return { success: res.success, error: res.error };
}

export async function blockTask(
  taskId: string,
  reason?: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const args = ["block", taskId];
  if (reason) args.push(reason);
  const res = await runKanban(args, { profile });
  return { success: res.success, error: res.error };
}

export async function unblockTask(
  taskId: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const res = await runKanban(["unblock", taskId], { profile });
  return { success: res.success, error: res.error };
}

export async function archiveTask(
  taskId: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const res = await runKanban(["archive", taskId], { profile });
  return { success: res.success, error: res.error };
}

export async function specifyTask(
  taskId: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const res = await runKanban(["specify", taskId], { profile });
  return { success: res.success, error: res.error };
}

export async function reclaimTask(
  taskId: string,
  reason?: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const args = ["reclaim", taskId];
  if (reason) args.push("--reason", reason);
  const res = await runKanban(args, { profile });
  return { success: res.success, error: res.error };
}

export async function commentTask(
  taskId: string,
  body: string,
  profile?: string,
): Promise<KanbanResult<void>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  if (!body.trim()) return { success: false, error: "Empty comment" };
  const res = await runKanban(["comment", taskId, body], { profile });
  return { success: res.success, error: res.error };
}

// Read-only virtual board: Claw3D's headquarters task board, stored at
// ~/.openclaw/claw3d/task-manager/tasks.json on the remote. The renderer
// surfaces this as a second board in the Kanban picker alongside the real
// hermes-agent boards. Only available in SSH tunnel mode — there is no
// equivalent local store for the Claw3D HQ list.
export async function listClaw3dHqTasks(): Promise<KanbanResult<KanbanTask[]>> {
  const conn = getConnectionConfig();
  if (conn.mode !== "ssh" || !conn.ssh) {
    return {
      success: false,
      error:
        "Claw3D HQ board is only available in SSH tunnel mode. Switch the connection mode in Settings to view it.",
    };
  }
  const res = await sshListClaw3dHqTasks(conn.ssh);
  if (!res.success) {
    return { success: false, error: res.error };
  }
  return { success: true, data: res.tasks ?? [] };
}

export async function dispatchOnce(
  dryRun = false,
  profile?: string,
): Promise<KanbanResult<unknown>> {
  if (isRemoteOnlyMode()) return unsupportedInRemote();
  const args = ["dispatch", "--json"];
  if (dryRun) args.push("--dry-run");
  const res = await runKanban(args, { profile, parseJson: true });
  return { success: res.success, error: res.error, data: res.data };
}
