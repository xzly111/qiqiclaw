import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");

describe("models library seeding", () => {
  it("does not seed static default models that bypass provider discovery", () => {
    const src = readFileSync(join(ROOT, "src/main/models.ts"), "utf-8");

    expect(src).not.toContain("DEFAULT_MODELS.map");
    expect(src).not.toContain('from "./default-models"');
    expect(src).toContain("const models: SavedModel[] = [];");
  });
});
