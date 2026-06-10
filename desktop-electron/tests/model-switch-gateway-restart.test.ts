import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");

describe("set-model-config gateway restart", () => {
  it("waits for the restarted local gateway before resolving model changes", () => {
    const src = readFileSync(join(ROOT, "src/main/index.ts"), "utf-8");
    const start = src.indexOf('ipcMain.handle(\n    "set-model-config"');
    const end = src.indexOf('ipcMain.handle("get-api-server-key-status"', start);
    const block = src.slice(start, end);

    expect(block).toContain("const changed =");
    expect(block).toContain("const restarted = await restartInstalledGateway(profile);");
    expect(block).toContain("if (!restarted) restartGateway(profile);");
    expect(block).toContain("await ensureLocalApiUsable(profile);");
  });
});
