import { describe, expect, it } from "vitest";
import { bytes, initials, scoreTone, secondsToClock, statusTone, titleCase } from "./format";

describe("format helpers", () => {
  it("formats countdown seconds as mm:ss", () => {
    expect(secondsToClock(0)).toBe("00:00");
    expect(secondsToClock(65)).toBe("01:05");
    expect(secondsToClock(-4)).toBe("00:00");
  });

  it("maps scores to semantic tones", () => {
    expect(scoreTone(90)).toBe("success");
    expect(scoreTone(60)).toBe("warning");
    expect(scoreTone(20)).toBe("danger");
  });

  it("maps lifecycle states to badge tones", () => {
    expect(statusTone("STORED")).toBe("success");
    expect(statusTone("RUNNING")).toBe("warning");
    expect(statusTone("FAILED")).toBe("danger");
    expect(statusTone("CONFIGURED")).toBe("neutral");
  });

  it("humanises enum labels and initials", () => {
    expect(titleCase("PAPER_DECRYPTED")).toBe("Paper Decrypted");
    expect(initials("Riya Sharma")).toBe("RS");
  });

  it("formats byte counts", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(1024)).toBe("1.0 KB");
    expect(bytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
