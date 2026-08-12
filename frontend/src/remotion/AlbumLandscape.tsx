import { Audio, AbsoluteFill, Img, Sequence, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { brandPhaseAtFrame, buildTimeline, formatTimestamp, globalFadeOpacity, trackAtFrame, trackLayerOpacityAtFrame, type TimelineTrack } from "./timeline";
import { buildThumbnailTypography, fitPosterFontSize, wholeWordWrapStyle, type ThumbnailPosterLayout } from "./thumbnailTypography";

export type AlbumVideoColors = {
  primary: string;
  secondary: string;
  accent: string;
  marker: string;
  scrim: string;
};

export type AlbumBrandProps = {
  enabled?: boolean;
  profile?: string;
  revision?: string | null;
  monogramUrl?: string;
  lockupUrl?: string;
  watermarkUrl?: string;
  openingSeconds?: number;
  closingSeconds?: number;
  thumbnailMode?: boolean;
  thumbnailStamp?: { enabled?: boolean; corner?: string; widthFraction?: number };
};

export type AlbumVideoProps = {
  artist: string;
  album: string;
  artworkUrl: string;
  displayFontUrl: string;
  displayFontItalicUrl?: string;
  utilityFontUrl: string;
  displayFontFamily: string;
  utilityFontFamily: string;
  colors: AlbumVideoColors;
  cinematicFinish: "Off" | "Subtle" | "Textured";
  reducedMotion?: boolean;
  descriptionNotes?: string;
  tracks: TimelineTrack[];
  includeAudio?: boolean;
  textureUrl?: string;
  fadeInSeconds?: number;
  fadeOutSeconds?: number;
  brand?: AlbumBrandProps;
  thumbnailEditorial?: {
    layout: "control" | "album-focus" | "instrumental-focus";
  };
};

function displayTitle(title: string): string {
  return title
    .replace(/^\s*\d{1,3}-\d{1,3}\s+/, "")
    .replace(/^\s*\d{1,3}\s*-\s*/, "")
    .replace(/\s*\+\s*/g, " / ")
    .replaceAll("_", " / ")
    .trim();
}

function withAlpha(hex: string, alpha: string): string {
  return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex + alpha : hex;
}

function fontFace(name: string, url: string, format: string, style = "normal"): string {
  return "@font-face{font-family:\"" + name + "\";src:url(\"" + url + "\") format(\"" + format + "\");font-weight:100 900;font-style:" + style + ";font-display:block;}";
}

function responsiveFontSize(width: number, text: string, baseFraction: number, longFraction: number): number {
  return Math.round(width * (text.length > 72 ? longFraction : baseFraction));
}

function ThumbnailPoster({ layout, artist, album, width, height, colors, displayFontFamily, utilityFontFamily, textShadow }: {
  layout: ThumbnailPosterLayout;
  artist: string;
  album: string;
  width: number;
  height: number;
  colors: AlbumVideoColors;
  displayFontFamily: string;
  utilityFontFamily: string;
  textShadow: string;
}) {
  const typography = buildThumbnailTypography({ layout, artist, album });
  const albumFocus = layout === "album-focus";
  const title = typography.titleLines[0];
  const titleSize = fitPosterFontSize(width, title, albumFocus ? 0.082 : 0.076, albumFocus ? 0.78 : 0.7);
  const lineWidth = Math.round(width * (albumFocus ? 0.19 : 0.14));
  return <AbsoluteFill style={{ display: "grid", placeItems: "center", padding: `${height * 0.12}px ${width * 0.1}px`, color: colors.secondary }}>
    <div style={{ width: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", transform: `translateY(${albumFocus ? -height * 0.015 : height * 0.005}px)`, textAlign: "center" }}>
      <div data-thumbnail-title={layout} style={{ color: albumFocus ? colors.primary : colors.secondary, fontFamily: displayFontFamily, fontSize: titleSize, fontWeight: 900, lineHeight: 0.92, letterSpacing: albumFocus ? "0.01em" : "-0.025em", maxWidth: width * (albumFocus ? 0.78 : 0.7), whiteSpace: "nowrap", textShadow, WebkitTextStroke: albumFocus ? `1px ${withAlpha(colors.scrim, "55")}` : undefined }}>{title}</div>
      {albumFocus ? <>
        <div aria-hidden="true" style={{ marginTop: height * 0.055, display: "flex", alignItems: "center", gap: Math.round(width * 0.014) }}>
          <span style={{ width: lineWidth, height: 2, backgroundColor: colors.marker, boxShadow: `0 2px 10px ${withAlpha(colors.scrim, "AA")}` }} />
          <span style={{ width: 13, height: 13, backgroundColor: colors.marker, transform: "rotate(45deg)", boxShadow: `0 2px 10px ${withAlpha(colors.scrim, "AA")}` }} />
          <span style={{ width: lineWidth, height: 2, backgroundColor: colors.marker, boxShadow: `0 2px 10px ${withAlpha(colors.scrim, "AA")}` }} />
        </div>
        <div style={{ marginTop: height * 0.045, color: colors.secondary, fontFamily: displayFontFamily, fontStyle: "italic", fontSize: fitPosterFontSize(width, typography.subtitle, 0.042, 0.52), lineHeight: 1, letterSpacing: "-0.02em", whiteSpace: "nowrap", textShadow }}>{typography.subtitle}</div>
      </> : <>
        <div aria-hidden="true" style={{ marginTop: height * 0.052, width: Math.round(width * 0.34), height: 2, background: `linear-gradient(90deg, transparent, ${colors.marker} 18%, ${colors.marker} 82%, transparent)`, boxShadow: `0 2px 10px ${withAlpha(colors.scrim, "AA")}` }} />
        <div style={{ marginTop: height * 0.035, padding: `${height * 0.012}px ${width * 0.026}px`, color: colors.primary, fontFamily: utilityFontFamily, fontSize: Math.round(width * 0.017), fontWeight: 800, lineHeight: 1, letterSpacing: "0.16em", whiteSpace: "nowrap", textShadow, borderTop: `2px solid ${withAlpha(colors.marker, "AA")}`, borderBottom: `2px solid ${withAlpha(colors.marker, "AA")}` }}>{typography.subtitle}</div>
      </>}
    </div>
  </AbsoluteFill>;
}

export function AlbumLandscape(props: AlbumVideoProps) {
  const frame = useCurrentFrame();
  const { width, height, fps } = useVideoConfig();
  const timeline = buildTimeline(props.tracks, fps);
  const brand = props.brand;
  const brandPhase = brand?.enabled && !brand.thumbnailMode ? brandPhaseAtFrame(frame, timeline.durationInFrames, fps, brand.openingSeconds ?? 1.75, brand.closingSeconds ?? 2.5) : { phase: "none" as const, opacity: 0 };
  const terminalClosing = brandPhase.phase === "closing";
  const active = trackAtFrame(timeline.tracks, frame);
  if (!active) {
    return <AbsoluteFill style={{ backgroundColor: props.colors.scrim, color: props.colors.secondary, display: "grid", placeItems: "center", fontFamily: props.utilityFontFamily }}>No validated Final Instrumentals configured.</AbsoluteFill>;
  }
  const localFrame = Math.max(0, frame - active.startFrame);
  const localSeconds = localFrame / fps;
  const progress = interpolate(localFrame, [0, Math.max(1, active.durationInFrames - 1)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const enter = interpolate(localFrame, [0, 18, 42], [0.92, 0.98, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const drift = props.reducedMotion ? 0 : Math.sin(frame / 170) * 8;
  const bloom = props.reducedMotion ? 0 : (props.cinematicFinish === "Off" ? 0 : 0.08 + Math.sin(localFrame / 32) * 0.025);
  const textShadow = "0 5px 28px " + withAlpha(props.colors.scrim, "CC");
  const crispTextShadow = "0 2px 0 " + withAlpha(props.colors.scrim, "CC") + ", 0 7px 20px " + withAlpha(props.colors.scrim, "EE");
  const markerColor = props.colors.marker;
  const sequenceColor = props.colors.accent;
  const activeColor = props.colors.accent;
  const trackLabel = displayTitle(active.title);
  const artistLabel = props.artist.trim().toUpperCase();
  const artistFontSize = responsiveFontSize(width, artistLabel, 0.069, 0.032);
  const albumFontSize = responsiveFontSize(width, props.album, 0.022, 0.016);
  const trackFontSize = responsiveFontSize(width, trackLabel, 0.032, 0.021);
  const fadeOpacity = props.brand?.thumbnailMode ? 1 : globalFadeOpacity(frame, timeline.durationInFrames, fps, props.fadeInSeconds ?? 1, props.fadeOutSeconds ?? 2);
  const audioVolume = (trackStartFrame: number, localFrame: number) => globalFadeOpacity(trackStartFrame + localFrame, timeline.durationInFrames, fps, props.fadeInSeconds ?? 1, props.fadeOutSeconds ?? 2);
  const brandUrl = brandPhase.phase === "opening" ? brand?.monogramUrl : brandPhase.phase === "closing" ? brand?.lockupUrl : undefined;
  const editorialOpacity = terminalClosing ? trackLayerOpacityAtFrame(frame, timeline.durationInFrames, fps, brand?.openingSeconds ?? 1.75, brand?.closingSeconds ?? 2.5) : 1;
  const stamp = brand?.thumbnailMode && brand.thumbnailStamp?.enabled && brand.watermarkUrl ? brand.thumbnailStamp : undefined;
  const stampInset = Math.round(width * 0.035);
  const stampStyle = stamp?.corner === "top-right" ? { top: stampInset, right: stampInset } : stamp?.corner === "bottom-left" ? { bottom: stampInset, left: stampInset } : stamp?.corner === "bottom-right" ? { bottom: stampInset, right: stampInset } : { top: stampInset, left: stampInset };
  const thumbnailLayout = brand?.thumbnailMode ? props.thumbnailEditorial?.layout ?? "control" : "control";
  const albumFocus = thumbnailLayout === "album-focus";
  const instrumentalFocus = thumbnailLayout === "instrumental-focus";

  return (
    <AbsoluteFill style={{ backgroundColor: props.colors.scrim, overflow: "hidden" }}>
      <style>{fontFace(props.displayFontFamily, props.displayFontUrl, "truetype") + (props.displayFontItalicUrl ? fontFace(props.displayFontFamily, props.displayFontItalicUrl, "truetype", "italic") : "") + fontFace(props.utilityFontFamily, props.utilityFontUrl, "woff2")}</style>
      <Img src={props.artworkUrl} style={{ position: "absolute", top: "7%", width: "100%", height: "110%", objectFit: "cover", transform: "scale(1.045) translate3d(" + drift + "px, 0, 0)" }} />
      <AbsoluteFill style={{ background: "linear-gradient(90deg, " + withAlpha(props.colors.scrim, "B8") + " 0%, " + withAlpha(props.colors.scrim, "55") + " 52%, " + withAlpha(props.colors.scrim, "A3") + " 100%)" }} />
      <AbsoluteFill style={{ background: "radial-gradient(circle at 58% 42%, " + withAlpha(activeColor, "33") + " 0%, transparent 34%), linear-gradient(180deg, transparent 42%, " + withAlpha(props.colors.scrim, "C2") + " 100%)", opacity: 0.75 + bloom }} />
      {albumFocus || instrumentalFocus ? <ThumbnailPoster layout={thumbnailLayout as ThumbnailPosterLayout} artist={artistLabel} album={props.album} width={width} height={height} colors={props.colors} displayFontFamily={props.displayFontFamily} utilityFontFamily={props.utilityFontFamily} textShadow={crispTextShadow} /> : <AbsoluteFill style={{ padding: height * 0.105 + "px " + width * 0.0625 + "px", color: props.colors.secondary, fontFamily: props.displayFontFamily, textRendering: "geometricPrecision", WebkitFontSmoothing: "antialiased" }}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(33%, .8fr) minmax(45%, 1.2fr)", gap: 60, alignItems: "start", height: "100%", opacity: enter * editorialOpacity }}>
          <div style={{ paddingTop: 6 }}>
            <div style={{ color: props.colors.primary, fontSize: artistFontSize, fontWeight: 900, lineHeight: 0.95, letterSpacing: "0em", maxWidth: width * 0.36, ...wholeWordWrapStyle, textShadow: crispTextShadow, WebkitTextStroke: "1px " + withAlpha(props.colors.scrim, "55") }}>{artistLabel}</div>
            <div style={{ marginTop: 34, color: props.colors.secondary, fontStyle: "italic", fontSize: albumFontSize, lineHeight: 1, letterSpacing: "-0.02em", maxWidth: width * 0.36, ...wholeWordWrapStyle, textShadow: crispTextShadow }}>{props.album}</div>
          </div>
          <div style={{ alignSelf: "center", minWidth: 0, paddingTop: height * 0.03 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 18, color: props.colors.secondary }}>
              <span style={{ color: sequenceColor, fontFamily: props.displayFontFamily, fontSize: Math.round(width * 0.031), lineHeight: 1, letterSpacing: "0em", fontVariantNumeric: "tabular-nums", flex: "0 0 auto", textShadow: crispTextShadow }}>{String(active.sequence).padStart(2, "0")}</span>
              <span aria-hidden="true" style={{ color: sequenceColor, fontFamily: props.utilityFontFamily, fontSize: Math.round(width * 0.026), fontWeight: 700, lineHeight: 1, opacity: 0.9, flex: "0 0 auto", textShadow: crispTextShadow }}>|</span>
              <span style={{ flex: "1 1 auto", minWidth: 0, fontSize: trackFontSize, lineHeight: 1.08, letterSpacing: "0.015em", ...wholeWordWrapStyle, textWrap: "balance", textShadow: crispTextShadow }}>{trackLabel}</span>
            </div>
            <div style={{ marginTop: 74, display: "flex", justifyContent: "space-between", color: props.colors.secondary, fontFamily: props.utilityFontFamily, fontSize: Math.round(width * 0.013), fontWeight: 700, textShadow }}>
              <span>{formatTimestamp(localSeconds)}</span>
              <span>{formatTimestamp(active.durationSeconds)}</span>
            </div>
            <div style={{ position: "relative", height: 4, marginTop: 18, borderRadius: 999, backgroundColor: withAlpha(props.colors.secondary, "88"), overflow: "hidden" }}>
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: (progress * 100) + "%", backgroundColor: activeColor, borderRadius: 999 }} />
            </div>
          </div>
        </div>
      </AbsoluteFill>}
      {brandUrl && <AbsoluteFill style={{ zIndex: 4, opacity: brandPhase.opacity, pointerEvents: "none" }}>
        {brandPhase.phase === "closing" && <div aria-hidden="true" style={{ position: "absolute", top: "50%", left: "50%", width: "42%", height: "18%", transform: "translate(-50%, -50%)", borderRadius: "50%", background: `radial-gradient(ellipse, ${withAlpha(props.colors.primary, "42")} 0%, ${withAlpha(props.colors.primary, "24")} 38%, transparent 76%)`, filter: "blur(18px)" }} />}
        <Img src={brandUrl} style={{ position: "absolute", top: brandPhase.phase === "opening" ? "8%" : "50%", left: brandPhase.phase === "opening" ? "86%" : "50%", transform: brandPhase.phase === "opening" ? "translateX(-50%)" : "translate(-50%, -50%)", width: brandPhase.phase === "opening" ? "15%" : "34%", height: "auto", objectFit: "contain" }} />
      </AbsoluteFill>}
      {stamp && brand?.watermarkUrl && <img aria-hidden="true" src={brand.watermarkUrl} style={{ position: "absolute", zIndex: 5, width: Math.round(width * (stamp.widthFraction ?? 0.045)), height: "auto", ...stampStyle, objectFit: "contain", opacity: 0.92, pointerEvents: "none" }} />}
      {props.textureUrl && props.cinematicFinish !== "Off" && <Img src={props.textureUrl} style={{ position: "absolute", zIndex: 6, width: "100%", height: "100%", opacity: props.cinematicFinish === "Textured" ? 0.11 : 0.055, mixBlendMode: "screen", imageRendering: "auto", pointerEvents: "none" }} />}
      {fadeOpacity < 1 && <AbsoluteFill style={{ backgroundColor: props.colors.scrim, opacity: 1 - fadeOpacity, pointerEvents: "none" }} />}
      {props.includeAudio !== false && timeline.tracks.map((track) => track.audioUrl ? <Sequence key={track.trackId} from={track.startFrame} durationInFrames={track.durationInFrames} layout="none"><Audio src={track.audioUrl} volume={(localFrame) => audioVolume(track.startFrame, localFrame)} /></Sequence> : null)}
    </AbsoluteFill>
  );
}
