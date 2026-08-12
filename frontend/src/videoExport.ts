export type VideoExportMode = "fast" | "reference";

export type VideoRenderStatus = "queued" | "running" | "stopping" | "complete" | "failed" | "cancelled" | "interrupted";

export function isVideoRenderActive(status: VideoRenderStatus | string | undefined): boolean {
  return status === "queued" || status === "running" || status === "stopping";
}

export function canRetryVideoRender(status: VideoRenderStatus | string | undefined): boolean {
  return status === "failed" || status === "cancelled" || status === "interrupted";
}

export function videoExportModeLabel(mode: VideoExportMode): string {
  return mode === "fast" ? "Fast · Recommended" : "Reference";
}

export function rendererElapsedMs(renderer: Record<string, unknown> | undefined): number | null {
  const evidence = renderer?.evidence;
  if (!evidence || typeof evidence !== "object") return null;
  const elapsedMs = (evidence as { elapsedMs?: unknown }).elapsedMs;
  return typeof elapsedMs === "number" && Number.isFinite(elapsedMs) && elapsedMs >= 0 ? elapsedMs : null;
}

export function formatElapsedMs(elapsedMs: number | null): string | null {
  if (elapsedMs === null) return null;
  return `${(elapsedMs / 1000).toFixed(1)} s video stage`;
}
