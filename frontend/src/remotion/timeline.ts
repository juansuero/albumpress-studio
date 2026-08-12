export const ALBUM_FPS = 30;

export type TimelineTrack = {
  trackId: string;
  sequence: number;
  title: string;
  durationSeconds: number;
  audioUrl?: string;
  outputId?: string;
  fileFingerprint?: string | null;
  finalPath?: string;
  startFrame?: number;
  durationInFrames?: number;
  originalDurationSeconds?: number;
  effectiveDurationSeconds?: number;
  trailingSilenceSeconds?: number;
  retainedTailSeconds?: number;
  proposedRemovalSeconds?: number;
  silenceStatus?: string;
  silenceConfidence?: string;
  preparationOverride?: string | null;
};

export type BuiltTimeline = {
  tracks: Array<TimelineTrack & { startFrame: number; durationInFrames: number }>;
  durationInFrames: number;
};

export type TimelineChapter = {
  trackId: string;
  sequence: number;
  title: string;
  startFrame: number;
  startSeconds: number;
};

export type BrandPhase = "none" | "opening" | "closing";

export type BrandTiming = {
  openingFrames: number;
  closingStartFrame: number;
  closingFrames: number;
};

export function buildBrandTiming(totalFrames: number, fps = ALBUM_FPS, openingSeconds = 1.75, closingSeconds = 2.5): BrandTiming {
  const openingFrames = Math.max(1, Math.min(Math.round(openingSeconds * fps), Math.floor(totalFrames / 2)));
  const closingFrames = Math.max(1, Math.min(Math.round(closingSeconds * fps), Math.floor(totalFrames / 2)));
  return { openingFrames, closingStartFrame: Math.max(openingFrames, totalFrames - closingFrames), closingFrames };
}

export function brandPhaseAtFrame(frame: number, totalFrames: number, fps = ALBUM_FPS, openingSeconds = 1.75, closingSeconds = 2.5): { phase: BrandPhase; opacity: number } {
  const timing = buildBrandTiming(totalFrames, fps, openingSeconds, closingSeconds);
  const fadeFrames = Math.max(1, Math.round(fps * 0.35));
  if (frame < timing.openingFrames) {
    const fadeIn = Math.min(1, frame / fadeFrames);
    const fadeOut = Math.min(1, (timing.openingFrames - 1 - frame) / fadeFrames);
    return { phase: "opening", opacity: Math.max(0, Math.min(1, fadeIn, fadeOut)) };
  }
  if (frame >= timing.closingStartFrame) {
    const localFrame = frame - timing.closingStartFrame;
    const fadeIn = Math.min(1, localFrame / fadeFrames);
    const fadeOut = Math.min(1, (totalFrames - 1 - frame) / fadeFrames);
    return { phase: "closing", opacity: Math.max(0, Math.min(1, fadeIn, fadeOut)) };
  }
  return { phase: "none", opacity: 0 };
}

export function trackLayerOpacityAtFrame(frame: number, totalFrames: number, fps = ALBUM_FPS, openingSeconds = 1.75, closingSeconds = 2.5): number {
  return frame >= buildBrandTiming(totalFrames, fps, openingSeconds, closingSeconds).closingStartFrame ? 0 : 1;
}

export function buildTimeline(tracks: TimelineTrack[], fps = ALBUM_FPS): BuiltTimeline {
  let cursor = 0;
  let cursorSeconds = 0;
  const built = tracks.map((track) => {
    const nextFrame = Math.round((cursorSeconds + track.durationSeconds) * fps);
    const durationInFrames = Math.max(1, nextFrame - cursor);
    const item = { ...track, startFrame: cursor, durationInFrames };
    cursor += durationInFrames;
    cursorSeconds += track.durationSeconds;
    return item;
  });
  return { tracks: built, durationInFrames: cursor };
}

export function trackAtFrame(timeline: BuiltTimeline["tracks"], frame: number) {
  return timeline.find((track, index) => {
    const next = timeline[index + 1];
    return frame >= track.startFrame && (!next || frame < next.startFrame);
  }) ?? timeline[timeline.length - 1];
}

export function humanizeTrackTitle(title: string): string {
  return title.replace(/^\s*\d{2}-\d{2}\s+/, "").replaceAll("_", " / ").trim();
}

export function buildChapters(tracks: TimelineTrack[], fps = ALBUM_FPS): TimelineChapter[] {
  return buildTimeline(tracks, fps).tracks.map((track) => ({
    trackId: track.trackId,
    sequence: track.sequence,
    title: humanizeTrackTitle(track.title),
    startFrame: track.startFrame,
    startSeconds: track.startFrame / fps,
  }));
}

export function globalFadeOpacity(frame: number, totalFrames: number, fps = ALBUM_FPS, fadeInSeconds = 1, fadeOutSeconds = 2): number {
  const fadeInFrames = Math.max(1, Math.round(fadeInSeconds * fps));
  const fadeOutFrames = Math.max(1, Math.round(fadeOutSeconds * fps));
  const fadeIn = Math.max(0, Math.min(1, frame / fadeInFrames));
  const fadeOutStart = Math.max(0, totalFrames - fadeOutFrames);
  const fadeOut = frame < fadeOutStart ? 1 : Math.max(0, Math.min(1, (totalFrames - 1 - frame) / fadeOutFrames));
  return Math.max(0, Math.min(1, Math.min(fadeIn, fadeOut)));
}

export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  return String(minutes).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
}
