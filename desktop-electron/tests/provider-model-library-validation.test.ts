import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");

describe("provider model library validation", () => {
  it("only adds provider-page models after /models discovery confirms the exact id", () => {
    const src = readFileSync(
      join(ROOT, "src/renderer/src/screens/Providers/Providers.tsx"),
      "utf-8",
    );

    expect(src).toContain("const discoveredModelSet = new Set(discovery.models);");
    expect(src).toContain('discovery.status === "ok"');
    expect(src).toContain("discoveredModelSet.has(trimmedModelName)");
    expect(src).toContain("if (!modelIsDiscoverable) return;");
    expect(src).toContain("window.qiqiclawAPI");
    expect(src).toContain(".addModel(");
  });

  it("enforces the same provider discovery check in the main add-model IPC", () => {
    const src = readFileSync(join(ROOT, "src/main/index.ts"), "utf-8");
    const start = src.indexOf('ipcMain.handle(\n    "add-model"');
    const end = src.indexOf('ipcMain.handle("remove-model"', start);
    const block = src.slice(start, end);

    expect(block).toContain("await discoverProviderModels(");
    expect(block).toContain('discovery.status !== "ok"');
    expect(block).toContain("!discovery.models.includes(cleanModel)");
    expect(block).toContain("throw new Error(");
  });

  it("surfaces a warning for manually typed models outside the provider list", () => {
    const src = readFileSync(
      join(ROOT, "src/renderer/src/screens/Providers/Providers.tsx"),
      "utf-8",
    );

    expect(src).toContain("settings.modelNotDiscovered");
  });
});
