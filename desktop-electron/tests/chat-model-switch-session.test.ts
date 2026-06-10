import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");

describe("chat model switching", () => {
  it("applies model picker changes without forcing a new backend session", () => {
    const src = readFileSync(
      join(ROOT, "src/renderer/src/screens/Chat/Chat.tsx"),
      "utf-8",
    );

    expect(src).toContain("const handleSelectModel = useCallback(");
    expect(src).toContain("const [isSwitchingModel, setIsSwitchingModel] = useState(false);");
    expect(src).toContain("if (isSwitchingModel) return;");
    expect(src).toContain("queueRef.current = [];");
    expect(src).toContain("await modelConfig.selectModel(provider, model, baseUrl);");
    const switchBlock = src.slice(
      src.indexOf("const handleSelectModel = useCallback("),
      src.indexOf("// Drag-and-drop", src.indexOf("const handleSelectModel = useCallback(")),
    );
    expect(switchBlock).not.toContain("setQiQiClawSessionId(null);");
    expect(src).toContain("isDisabled={isSwitchingModel}");
    expect(src).toContain("onSelectModel={handleSelectModel}");
    expect(src).not.toContain("onSelectModel={modelConfig.selectModel}");
  });

  it("waits for model picker selection before closing or accepting another click", () => {
    const src = readFileSync(
      join(ROOT, "src/renderer/src/screens/Chat/ModelPicker.tsx"),
      "utf-8",
    );

    expect(src).toContain(") => Promise<void> | void;");
    expect(src).toContain("const [isSelecting, setIsSelecting] = useState(false);");
    expect(src).toContain("await onSelectModel(provider, model, baseUrl);");
    expect(src).toContain("disabled={isSelecting}");
  });

  it("disables chat submission while a model switch is being applied", () => {
    const src = readFileSync(
      join(ROOT, "src/renderer/src/screens/Chat/ChatInput.tsx"),
      "utf-8",
    );

    expect(src).toContain("isDisabled?: boolean;");
    expect(src).toContain("if (isDisabled) return;");
    expect(src).toContain("disabled={isDisabled}");
    expect(src).toContain("!isDisabled &&");
  });

  it("keeps Base URL when selecting custom credential-pool provider ids", () => {
    const src = readFileSync(
      join(ROOT, "src/renderer/src/screens/Chat/hooks/useModelConfig.ts"),
      "utf-8",
    );

    expect(src).toContain('clean === "custom" || clean.startsWith("custom:")');
    expect(src).toContain("isCustomProvider(provider) ? baseUrl :");
  });

  it("adds active model context when visible history is sent to any backend session", () => {
    const src = readFileSync(join(ROOT, "src/main/qiqiclaw.ts"), "utf-8");

    expect(src).toContain("if (history && history.length > 0)");
    expect(src).not.toContain("if (!_resumeSessionId && history && history.length > 0)");
    expect(src).toContain("The active model for this request is");
    expect(src).toContain("ignore any earlier conversation text that described a previous active model");
  });
});
