import { describe, expect, it } from "vitest";
import { brandPhaseAtFrame, buildBrandTiming, buildChapters, buildTimeline, formatTimestamp, globalFadeOpacity, humanizeTrackTitle, trackAtFrame, trackLayerOpacityAtFrame } from "./remotion/timeline";

describe("Album Landscape timeline", () => {
  it("keeps cumulative frame boundaries contiguous", () => {
    const timeline = buildTimeline([
      { trackId: "one", sequence: 1, title: "One", durationSeconds: 1.01 },
      { trackId: "two", sequence: 2, title: "Two", durationSeconds: 1.01 },
      { trackId: "three", sequence: 3, title: "Three", durationSeconds: 0.5 },
    ]);

    expect(timeline.tracks.map((track) => [track.startFrame, track.durationInFrames])).toEqual([
      [0, 30],
      [30, 31],
      [61, 15],
    ]);
    expect(timeline.durationInFrames).toBe(76);
  });

  it("selects the next Track exactly at its boundary", () => {
    const timeline = buildTimeline([
      { trackId: "one", sequence: 1, title: "One", durationSeconds: 1 },
      { trackId: "two", sequence: 2, title: "Two", durationSeconds: 1 },
    ]);

    expect(trackAtFrame(timeline.tracks, 29)?.trackId).toBe("one");
    expect(trackAtFrame(timeline.tracks, 30)?.trackId).toBe("two");
  });

  it("formats progress timestamps consistently", () => {
    expect(formatTimestamp(0)).toBe("00:00");
    expect(formatTimestamp(125.9)).toBe("02:05");
    expect(formatTimestamp(-1)).toBe("00:00");
  });

  it("uses effective durations for chapters and humanizes source titles", () => {
    const tracks = [
      { trackId: "one", sequence: 1, title: "01-01 Long_Title", durationSeconds: 5.2, originalDurationSeconds: 7.2 },
      { trackId: "two", sequence: 2, title: "02-02 Next_Title", durationSeconds: 2.4, originalDurationSeconds: 2.4 },
    ];

    expect(humanizeTrackTitle(tracks[0].title)).toBe("Long / Title");
    expect(buildChapters(tracks).map((chapter) => [chapter.startFrame, chapter.startSeconds])).toEqual([[0, 0], [156, 5.2]]);
  });

  it("keeps only global fade frames and never fades an intermediate Track", () => {
    expect(globalFadeOpacity(0, 300)).toBe(0);
    expect(globalFadeOpacity(30, 300)).toBe(1);
    expect(globalFadeOpacity(150, 300)).toBe(1);
    expect(globalFadeOpacity(299, 300)).toBe(0);
  });

  it("keeps branded idents inside the same total duration", () => {
    const timing = buildBrandTiming(300, 30);
    expect(timing.openingFrames).toBe(53);
    expect(timing.closingFrames).toBe(75);
    expect(timing.closingStartFrame).toBe(225);
    expect(brandPhaseAtFrame(60, 300, 30).phase).toBe("none");
    expect(brandPhaseAtFrame(240, 300, 30).phase).toBe("closing");
    expect(brandPhaseAtFrame(100, 300, 30).opacity).toBe(0);
  });

  it("makes the closing a terminal Track-UI state through the last frame", () => {
    const totalFrames = 300;
    const timing = buildBrandTiming(totalFrames, 30);
    const frames = [timing.closingStartFrame - 1, timing.closingStartFrame, timing.closingStartFrame + 37, totalFrames - 2, totalFrames - 1];

    expect(frames.map((frame) => trackLayerOpacityAtFrame(frame, totalFrames, 30))).toEqual([1, 0, 0, 0, 0]);
    expect(frames.slice(1).map((frame) => brandPhaseAtFrame(frame, totalFrames, 30).phase)).toEqual(["closing", "closing", "closing", "closing"]);
    expect(globalFadeOpacity(totalFrames - 1, totalFrames, 30)).toBe(0);
  });
});
