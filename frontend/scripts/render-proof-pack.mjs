import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";
import { bundle } from "@remotion/bundler";
import { renderMedia, renderStill, selectComposition } from "@remotion/renderer";

const inputPath = process.argv[2];
if (!inputPath) throw new Error("A Proof Pack input JSON path is required.");
const input = JSON.parse(await fs.readFile(inputPath, "utf8"));
const snapshot = input.snapshot;
const projectRoot = path.resolve(snapshot.projectFolder);
const assetPaths = new Map();
const token = crypto.randomBytes(16).toString("hex");
const mimeTypes = {
  artwork: "image/png",
  texture: "image/png",
  displayFont: "font/ttf",
  utilityFont: "font/woff2",
  "brand-monogram": "image/png",
  "brand-lockup": "image/png",
  "brand-watermark": "image/png",
  "brand-vector": "image/svg+xml",
  "brand-approvalManifest": "application/json",
  "audio-1": "audio/wav",
  "audio-2": "audio/wav",
  "audio-3": "audio/wav",
  "audio-4": "audio/wav",
  "audio-5": "audio/wav",
  "audio-6": "audio/wav",
  "audio-7": "audio/wav",
  "audio-8": "audio/wav",
  "audio-9": "audio/wav",
  "audio-10": "audio/wav",
};

function emit(event) {
  process.stdout.write(JSON.stringify(event) + "\n");
}

function assertInside(relativePath) {
  const candidate = path.resolve(projectRoot, relativePath);
  const relative = path.relative(projectRoot, candidate);
  if (relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) throw new Error("Proof asset escapes the Project Folder: " + relativePath);
  return candidate;
}

for (const [key, asset] of Object.entries(snapshot.assets ?? {})) {
  if (!asset || typeof asset.relativePath !== "string") throw new Error("Malformed Proof Pack asset: " + key);
  const assetPath = assertInside(asset.relativePath);
  const stat = await fs.stat(assetPath);
  if (!stat.isFile()) throw new Error("Proof Pack asset is not a file: " + key);
  assetPaths.set(key, assetPath);
}

const server = http.createServer(async (request, response) => {
  try {
    const prefix = "/asset/" + token + "/";
    if (!request.url?.startsWith(prefix)) {
      response.writeHead(404);
      response.end();
      return;
    }
    const key = decodeURIComponent(request.url.slice(prefix.length).split("?", 1)[0]);
    const filePath = assetPaths.get(key);
    if (!filePath) {
      response.writeHead(404);
      response.end();
      return;
    }
    const stat = await fs.stat(filePath);
    let start = 0;
    let end = stat.size - 1;
    let status = 200;
    const range = request.headers.range;
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!match) {
        response.writeHead(416, { "Content-Range": `bytes */${stat.size}` });
        response.end();
        return;
      }
      if (match[1]) start = Number(match[1]);
      if (match[2]) end = Number(match[2]);
      if (!match[1] && match[2]) start = Math.max(0, stat.size - Number(match[2]));
      end = Math.min(end, stat.size - 1);
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= stat.size || end < start) {
        response.writeHead(416, { "Content-Range": `bytes */${stat.size}` });
        response.end();
        return;
      }
      status = 206;
    }
    const headers = {
      "Access-Control-Allow-Origin": "*",
      "Accept-Ranges": "bytes",
      "Content-Length": String(end - start + 1),
      "Content-Type": mimeTypes[key] ?? (key.startsWith("audio-") ? "audio/wav" : "application/octet-stream"),
      "Content-Disposition": "inline",
    };
    if (status === 206) headers["Content-Range"] = `bytes ${start}-${end}/${stat.size}`;
    response.writeHead(status, headers);
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    const handle = await fs.open(filePath, "r");
    const stream = handle.createReadStream({ start, end });
    stream.on("close", () => void handle.close());
    stream.pipe(response);
  } catch (error) {
    response.writeHead(500);
    response.end(String(error?.message ?? error));
  }
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});

const { port } = server.address();
const assetUrl = (key) => `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(key)}`;

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const child = spawn("ffmpeg", args, { cwd: projectRoot, windowsHide: true });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", reject);
    child.on("close", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`FFmpeg audio mux failed (code=${code}, signal=${signal ?? "none"}): ${stderr.slice(-4000)}`));
    });
  });
}

function audioMuxArgs(snapshotValue, artifact, videoOnlyPath, outputPath) {
  const fps = Number(snapshotValue.expected?.fps ?? 30);
  const totalFrames = Number(snapshotValue.expected?.frameCount ?? snapshotValue.tracks.reduce((sum, track) => sum + Number(track.durationInFrames ?? Math.round(Number(track.durationSeconds) * fps)), 0));
  const startFrame = Number(artifact.startFrame);
  const endFrame = Number(artifact.endFrame);
  const endFrameExclusive = endFrame + 1;
  const segments = [];
  for (const track of snapshotValue.tracks) {
    const trackStart = Number(track.startFrame ?? 0);
    const trackEnd = trackStart + Number(track.durationInFrames ?? Math.round(Number(track.durationSeconds) * fps));
    const overlapStart = Math.max(startFrame, trackStart);
    const overlapEnd = Math.min(endFrameExclusive, trackEnd);
    if (overlapEnd <= overlapStart) continue;
    const audioPath = assetPaths.get(track.audioKey);
    if (!audioPath) throw new Error(`Missing audio asset for ${track.trackId}.`);
    segments.push({ audioPath, start: (overlapStart - trackStart) / fps, duration: (overlapEnd - overlapStart) / fps });
  }
  if (segments.length === 0) throw new Error(`No audio segments overlap proof frames ${startFrame}-${endFrame}.`);

  const filter = [];
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    filter.push(`[${index + 1}:a]atrim=start=${segment.start.toFixed(6)}:end=${(segment.start + segment.duration).toFixed(6)},asetpts=PTS-STARTPTS[a${index}]`);
  }
  const concat = segments.map((_, index) => `[a${index}]`).join("") + `concat=n=${segments.length}:v=0:a=1,aresample=48000:async=1`;
  const fadeOps = [];
  const fadeInSeconds = Number(snapshotValue.props?.fadeInSeconds ?? 1);
  const fadeOutSeconds = Number(snapshotValue.props?.fadeOutSeconds ?? 2);
  if (startFrame === 0 && fadeInSeconds > 0) fadeOps.push(`afade=t=in:st=0:d=${fadeInSeconds.toFixed(6)}`);
  if (endFrameExclusive >= totalFrames && fadeOutSeconds > 0) {
    const fadeStart = Math.max(0, totalFrames / fps - fadeOutSeconds - startFrame / fps);
    fadeOps.push(`afade=t=out:st=${fadeStart.toFixed(6)}:d=${fadeOutSeconds.toFixed(6)}`);
  }
  filter.push(`${concat}${fadeOps.length ? `,${fadeOps.join(",")}` : ""}[proofAudio]`);

  const args = ["-hide_banner", "-loglevel", "error", "-y", "-i", videoOnlyPath];
  for (const segment of segments) args.push("-i", segment.audioPath);
  args.push("-filter_complex", filter.join(";"), "-map", "0:v:0", "-map", "[proofAudio]", "-t", ((endFrameExclusive - startFrame) / fps).toFixed(6), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", outputPath);
  return args;
}

const brandBase = snapshot.props?.brand;
const brand = brandBase?.enabled ? {
  ...brandBase,
  monogramUrl: assetUrl(brandBase.monogramKey ?? "brand-monogram"),
  lockupUrl: assetUrl(brandBase.lockupKey ?? "brand-lockup"),
  watermarkUrl: assetUrl(brandBase.watermarkKey ?? "brand-watermark"),
} : brandBase;

try {
  emit({ stage: "bundling", progress: 0, message: "Bundling the shared AlbumLandscape composition once." });
  const serveUrl = await bundle({ entryPoint: path.resolve("src/remotion/index.tsx"), rootDir: path.resolve(".") });
  const compositionProps = {
    ...snapshot.props,
    artworkUrl: assetUrl(snapshot.props.artworkKey ?? "artwork"),
    textureUrl: snapshot.props.textureKey ? assetUrl(snapshot.props.textureKey) : undefined,
    displayFontUrl: assetUrl(snapshot.props.displayFontKey ?? "displayFont"),
    displayFontItalicUrl: snapshot.props.displayFontItalicKey ? assetUrl(snapshot.props.displayFontItalicKey) : undefined,
    utilityFontUrl: assetUrl(snapshot.props.utilityFontKey ?? "utilityFont"),
    brand,
  };
  const composition = await selectComposition({ serveUrl, id: "AlbumLandscape", inputProps: compositionProps });
  const artifacts = input.artifacts ?? [];
  for (let index = 0; index < artifacts.length; index += 1) {
    const artifact = artifacts[index];
    const isStill = artifact.kind === "still" || artifact.kind === "thumbnail";
    const props = {
      ...compositionProps,
      includeAudio: false,
      tracks: snapshot.tracks.map((track) => ({ ...track, audioUrl: undefined })),
      brand: artifact.kind === "thumbnail" && brand ? { ...brand, thumbnailMode: true } : brand,
    };
    emit({ stage: isStill ? "rendering-still" : "rendering-video", progress: index / artifacts.length, artifact: artifact.caseId, message: `Rendering ${artifact.caseId}.` });
    if (isStill) {
      await renderStill({ composition, serveUrl, frame: Number(artifact.frame ?? 0), output: artifact.outputPath, inputProps: props, scale: artifact.kind === "thumbnail" ? 2 / 3 : 1 });
    } else {
      const videoOnlyPath = `${artifact.outputPath}.video-only.mp4`;
      await renderMedia({
        composition,
        serveUrl,
        codec: "h264",
        audioCodec: "aac",
        outputLocation: videoOnlyPath,
        inputProps: props,
        frameRange: [Number(artifact.startFrame), Number(artifact.endFrame)],
        concurrency: 2,
        crf: 23,
        pixelFormat: "yuv420p",
        colorSpace: "bt709",
      });
      emit({ stage: "muxing-audio", progress: index / artifacts.length, artifact: artifact.caseId, message: `Muxing real audio for ${artifact.caseId}.` });
      await runFfmpeg(audioMuxArgs(snapshot, artifact, videoOnlyPath, artifact.outputPath));
      await fs.rm(videoOnlyPath, { force: true });
    }
    emit({ stage: "artifact-complete", progress: (index + 1) / artifacts.length, artifact: artifact.caseId, message: `${artifact.caseId} rendered.` });
  }
  emit({ stage: "complete", progress: 1, message: "Proof Pack artifacts rendered; backend will validate FFprobe and frames." });
} finally {
  await new Promise((resolve) => server.close(resolve));
}
