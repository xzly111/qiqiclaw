import { describe, expect, it } from "vitest";
import { delimiter } from "path";
import {
  buildQiqiclawEnv,
  getEnhancedPath,
  qiqiclawCliArgs,
  QIQICLAW_HOME,
  QIQICLAW_PYTHON,
  QIQICLAW_SCRIPT,
  hasQiqiclawCli,
} from "../src/main/installer";

describe("installer platform wiring", () => {
  it("uses the platform path delimiter in the enhanced PATH", () => {
    const enhancedPath = getEnhancedPath();

    expect(enhancedPath).toContain(process.env.PATH || "");
    expect(enhancedPath.split(delimiter).length).toBeGreaterThan(1);
  });

  it("builds platform-specific QiQiClaw CLI invocation args", () => {
    const args = qiqiclawCliArgs(["--version"]);

    if (process.platform === "win32") {
      expect(args).toEqual(["-m", "qiqiclaw_cli.main", "--version"]);
      // Use `pythonw.exe` (Windows-subsystem) instead of `python.exe` so
      // child spawns don't flash a blank console window before
      // `windowsHide`/CREATE_NO_WINDOW takes effect — see issue #342.
      expect(QIQICLAW_PYTHON).toMatch(/venv[\\/]Scripts[\\/]pythonw\.exe$/);
      expect(QIQICLAW_SCRIPT).toMatch(/venv[\\/]Scripts[\\/]qiqiclaw\.exe$/);
      return;
    }

    expect(args[1]).toBe("--version");
    if (hasQiqiclawCli()) {
      expect(args[0]).not.toBe("");
    } else {
      expect(args[0]).toBe(QIQICLAW_SCRIPT);
    }
    expect(QIQICLAW_PYTHON).toMatch(/venv[\\/]bin[\\/]python$/);
  });

  it("exports QiQiClaw home env var for CLI subprocesses", () => {
    const env = buildQiqiclawEnv({ TERM: "dumb" });

    expect(env.QIQICLAW_HOME).toBe(QIQICLAW_HOME);
    expect(env.QIQICLAW_HOME).toBe(QIQICLAW_HOME);
    expect(env.TERM).toBe("dumb");
    expect(env.PATH).toBe(getEnhancedPath());
  });
});
