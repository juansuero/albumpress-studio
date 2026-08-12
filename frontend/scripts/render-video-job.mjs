import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";

const inputPath = process.argv[2];
if (!inputPath) {
  throw new Error("A render input JSON path is required.");
}

const input = JSON.parse(await fs.readFile(inputPath, "utf8"));
const snapshot = input.snapshot;
const projectRoot = path.resolve(snapshot.projectFolder);
const assets = snapshot.assets;
const assetPaths = new Map();

function assertInsideProject(relativePath) {
  const candidate = path.resolve(projectRoot, relativePath);
  const relative = path.relative(projectRoot, candidate);
  if (relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) {
    throw new Error("Render asset escapes the Project Folder: " + relativePath);
  }
  return candidate;
}

for (const [key, asset] of Object.entries(assets)) {
  if (!asset || typeof asset.relativePath !== "string") {
    throw new Error("Render snapshot asset is malformed: " + key);
  }
  const assetPath = assertInsideProject(asset.relativePath);
  const stat = await fs.stat(assetPath);
  if (!stat.isFile()) throw new Error("Render snapshot asset is not a file: " + key);
  assetPaths.set(key, assetPath);
}

const token = crypto.randomBytes(16).toString("hex");
const mimeTypes = {
  artwork: "image/png",
  displayFont: "font/ttf",
  utilityFont: "font/woff2",
  "brand-monogram": "image/png",
  "brand-lockup": "image/png",
  "brand-watermark": "image/png",
  "brand-vector": "image/svg+xml",
  "brand-approvalManifest": "application/json",
  "audio-1": "audio/wav",
  "audio-2": "audio/wav",
};

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
      if (!match[1] && match[2]) {
        const suffix = Number(match[2]);
        start = Math.max(0, stat.size - suffix);
        end = stat.size - 1;
      }
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

function emit(event) {
  process.stdout.write(JSON.stringify(event) + "\n");
}

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});

const { port } = server.address();
const assetUrl = (key) => `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(key)}`;
const brand = snapshot.props.brand?.enabled ? {
  ...snapshot.props.brand,
  monogramUrl: assetUrl(snapshot.props.brand.monogramKey),
  lockupUrl: assetUrl(snapshot.props.brand.lockupKey),
  watermarkUrl: assetUrl(snapshot.props.brand.watermarkKey),
} : snapshot.props.brand;
const props = {
  ...snapshot.props,
  artworkUrl: assetUrl(snapshot.props.artworkKey),
  displayFontUrl: assetUrl(snapshot.props.displayFontKey),
  displayFontItalicUrl: snapshot.props.displayFontItalicKey ? assetUrl(snapshot.props.displayFontItalicKey) : undefined,
  utilityFontUrl: assetUrl(snapshot.props.utilityFontKey),
  includeAudio: true,
  brand,
  tracks: snapshot.tracks.map((track) => ({ ...track, audioUrl: assetUrl(track.audioKey) })),
};

try {
  emit({ stage: "bundling", progress: 0, message: "Bundling the shared Remotion composition once." });
  const serveUrl = await bundle({
    entryPoint: path.resolve("src/remotion/index.tsx"),
    rootDir: path.resolve("."),
  });
  emit({ stage: "selecting", progress: 0.15, message: "Selecting the shared Album Landscape composition." });
  const composition = await selectComposition({ serveUrl, id: "AlbumLandscape", inputProps: props });
  emit({ stage: "rendering", progress: 0.2, message: "Rendering one MP4 with bounded concurrency." });
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    audioCodec: "aac",
    outputLocation: input.outputPath,
    inputProps: props,
    concurrency: Number(input.concurrency) || 2,
    crf: 23,
    pixelFormat: "yuv420p",
    colorSpace: "bt709",
    onProgress: (progress) => emit({ stage: "rendering", progress: 0.2 + Math.min(0.8, Math.max(0, Number(progress.progress) || 0) * 0.8), message: "Rendering MP4." }),
  });
  emit({ stage: "validating", progress: 1, message: "Renderer completed; backend will run FFprobe validation." });
} finally {
  await new Promise((resolve) => server.close(resolve));
}
