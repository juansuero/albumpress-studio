import { describe, expect, it } from "vitest";

import { buildThumbnailTypography, wholeWordWrapStyle } from "./thumbnailTypography";

describe("thumbnail typography", () => {
  it("keeps complete words on explicit lines for the Little Songs poster variants", () => {
    const albumFocus = buildThumbnailTypography({
      layout: "album-focus",
      artist: "COLTER WALL",
      album: "Little Songs",
    });
    const instrumentalFocus = buildThumbnailTypography({
      layout: "instrumental-focus",
      artist: "COLTER WALL",
      album: "Little Songs",
    });

    expect(albumFocus).toEqual({
      titleLines: ["COLTER WALL"],
      subtitle: "Little Songs",
    });
    expect(instrumentalFocus).toEqual({
      titleLines: ["Little Songs"],
      subtitle: "COLTER WALL",
    });
    expect([...albumFocus.titleLines, ...instrumentalFocus.titleLines]).not.toContain("COLTE");
    expect([...albumFocus.titleLines, ...instrumentalFocus.titleLines]).not.toContain("INSTRUMENT");
    expect(JSON.stringify({ albumFocus, instrumentalFocus })).not.toMatch(/full album|instrumental album/i);
  });

  it("allows the control frame to wrap only at word boundaries", () => {
    expect(wholeWordWrapStyle).toEqual({ overflowWrap: "normal", wordBreak: "normal" });
  });
});
