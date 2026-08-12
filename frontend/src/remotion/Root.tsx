import { Composition, type CalculateMetadataFunction } from "remotion";
import { AlbumLandscape, type AlbumVideoProps } from "./AlbumLandscape";
import { buildTimeline, type TimelineTrack } from "./timeline";

const defaultTrack: TimelineTrack = { trackId: "default", sequence: 1, title: "Preview Track", durationSeconds: 1 };

export const defaultAlbumVideoProps: AlbumVideoProps = {
  artist: "Artist",
  album: "Album",
  artworkUrl: "",
  displayFontUrl: "",
  displayFontItalicUrl: "",
  utilityFontUrl: "",
  displayFontFamily: "Bevan",
  utilityFontFamily: "Atkinson Hyperlegible Next",
  colors: { primary: "#E4785D", secondary: "#F1ECE3", accent: "#E4785D", marker: "#D99A59", scrim: "#17151A" },
  cinematicFinish: "Textured",
  textureUrl: "",
  fadeInSeconds: 1,
  fadeOutSeconds: 2,
  reducedMotion: false,
  tracks: [defaultTrack],
  includeAudio: false,
  brand: { enabled: false },
};

const calculateAlbumMetadata: CalculateMetadataFunction<AlbumVideoProps> = ({ props }) => {
  const timeline = buildTimeline(props.tracks);
  return { durationInFrames: timeline.durationInFrames, fps: 30, width: 1920, height: 1080, props: { ...props, tracks: timeline.tracks } };
};

export const RemotionRoot = () => (
  <Composition
    id="AlbumLandscape"
    component={AlbumLandscape}
    width={1920}
    height={1080}
    fps={30}
    durationInFrames={30}
    defaultProps={defaultAlbumVideoProps}
    calculateMetadata={calculateAlbumMetadata}
  />
);
