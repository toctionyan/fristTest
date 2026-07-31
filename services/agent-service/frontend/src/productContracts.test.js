import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("responsive product contract", () => {
  it("places the agent workspace before order detail on mobile", () => {
    const css = readFileSync("./src/styles.css", "utf8");
    const mobile = css.slice(css.indexOf("@media (max-width: 760px)"));
    expect(mobile).toContain(".agent-pane");
    expect(mobile).toContain("grid-row: 1");
    expect(mobile).toContain(".orders-pane");
    expect(mobile).toContain("grid-row: 2");
  });
});
