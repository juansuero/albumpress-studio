import { describe, expect, it } from "vitest";
import { canRetryVideoRender, formatElapsedMs, isVideoRenderActive, rendererElapsedMs, videoExportModeLabel } from "./videoExport";

describe("Fast Export UI state", () => {
  it("keeps the Fast mode visibly recommended", () => {
    expect(videoExportModeLabel("fast")).toBe("Fast · Recommended");
    expect(videoExportModeLabel("reference")).toBe("Reference");
  });

  it("disables duplicate starts only during an active render", () => {
    expect(isVideoRenderActive("queued")).toBe(true);
    expect(isVideoRenderActive("running")).toBe(true);
    expect(isVideoRenderActive("stopping")).toBe(true);
    expect(isVideoRenderActive("complete")).toBe(false);
  });

  it("offers recovery for failed, cancelled and interrupted jobs", () => {
    expect(canRetryVideoRender("failed")).toBe(true);
    expect(canRetryVideoRender("cancelled")).toBe(true);
    expect(canRetryVideoRender("interrupted")).toBe(true);
    expect(canRetryVideoRender("complete")).toBe(false);
  });

  it("only displays elapsed renderer evidence when it is numeric", () => {
    expect(rendererElapsedMs({ evidence: { elapsedMs: 2450 } })).toBe(2450);
    expect(formatElapsedMs(2450)).toBe("2.5 s video stage");
    expect(rendererElapsedMs({ evidence: { elapsedMs: "unknown" } })).toBeNull();
    expect(formatElapsedMs(null)).toBeNull();
  });
});
