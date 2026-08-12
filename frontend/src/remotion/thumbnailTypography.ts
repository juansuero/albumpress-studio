export type ThumbnailPosterLayout = "album-focus" | "instrumental-focus";

export type ThumbnailTypography = {
  titleLines: string[];
  subtitle: string;
};

export const wholeWordWrapStyle = {
  overflowWrap: "normal" as const,
  wordBreak: "normal" as const,
};

type ThumbnailTypographyInput = {
  layout: ThumbnailPosterLayout;
  artist: string;
  album: string;
};

function clean(value: string | undefined): string {
  return (value ?? "").trim().replace(/\s+/g, " ");
}

export function buildThumbnailTypography(input: ThumbnailTypographyInput): ThumbnailTypography {
  const artist = clean(input.artist).toUpperCase();
  const album = clean(input.album);
  if (input.layout === "album-focus") {
    return { titleLines: [artist], subtitle: album };
  }
  return { titleLines: [album], subtitle: artist };
}

export function fitPosterFontSize(width: number, text: string, baseFraction: number, maxWidthFraction: number): number {
  const base = width * baseFraction;
  const estimatedFit = (width * maxWidthFraction) / Math.max(1, clean(text).length * 0.72);
  return Math.round(Math.max(width * 0.035, Math.min(base, estimatedFit)));
}
