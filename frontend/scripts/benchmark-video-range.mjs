import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";

const [manifestPath, outputDir, concurrencyArg, finishArg = "Textured"] = process.argv.slice(2);
if (!manifestPath || !outputDir || !concurrencyArg) throw new Error("Usage: node benchmark-video-range.mjs <render-manifest.json> <output-dir> <concurrency> [finish]");

const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
const snapshot = manifest.snapshot;
const projectRoot = path.resolve(snapshot.projectFolder);
const assets = new Map();

function projectAsset(relativePath) {
  const absolute = path.resolve(projectRoot, relativePath);
  const relative = path.relative(projectRoot, absolute);
  if (relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) throw new Error(`Asset escapes the Project Folder: ${relativePath}`);
  return absolute;
}

for (const [key, asset] of Object.entries(snapshot.assets)) {
  const assetPath = projectAsset(asset.relativePath);
  const stat = await fs.stat(assetPath);
  if (!stat.isFile()) throw new Error(`Asset is not a file: ${key}`);
  assets.set(key, assetPath);
}

const token = Math.random().toString(16).slice(2);
const mimeTypes = { artwork: "image/png", displayFont: "font/ttf", utilityFont: "font/woff2" };
const server = http.createServer(async (request, response) => {
  try {
    const prefix = `/asset/${token}/`;
    if (!request.url?.startsWith(prefix)) { response.writeHead(404); response.end(); return; }
    const key = decodeURIComponent(request.url.slice(prefix.length).split("?", 1)[0]);
    const filePath = assets.get(key);
    if (!filePath) { response.writeHead(404); response.end(); return; }
    const stat = await fs.stat(filePath);
    let start = 0;
    let end = stat.size - 1;
    let status = 200;
    const range = request.headers.range;
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!match) { response.writeHead(416, { "Content-Range": `bytes */${stat.size}` }); response.end(); return; }
      if (match[1]) start = Number(match[1]);
      if (match[2]) end = Number(match[2]);
      if (!match[1] && match[2]) { start = Math.max(0, stat.size - Number(match[2])); end = stat.size - 1; }
      end = Math.min(end, stat.size - 1);
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= stat.size || end < start) { response.writeHead(416, { "Content-Range": `bytes */${stat.size}` }); response.end(); return; }
      status = 206;
    }
    const headers = { "Access-Control-Allow-Origin": "*", "Accept-Ranges": "bytes", "Content-Length": String(end - start + 1), "Content-Type": mimeTypes[key] ?? (key.startsWith("audio-") ? "audio/wav" : "application/octet-stream"), "Content-Disposition": "inline" };
    if (status === 206) headers["Content-Range"] = `bytes ${start}-${end}/${stat.size}`;
    response.writeHead(status, headers);
    if (request.method === "HEAD") { response.end(); return; }
    const handle = await fs.open(filePath, "r");
    const stream = handle.createReadStream({ start, end });
    stream.on("close", () => void handle.close());
    stream.pipe(response);
  } catch (error) { response.writeHead(500); response.end(String(error?.message ?? error)); }
});

await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const { port } = server.address();
const assetUrl = (key) => `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(key)}`;
const props = {
  ...snapshot.props,
  cinematicFinish: finishArg,
  artworkUrl: assetUrl(snapshot.props.artworkKey),
  displayFontUrl: assetUrl(snapshot.props.displayFontKey),
  displayFontItalicUrl: snapshot.props.displayFontItalicKey ? assetUrl(snapshot.props.displayFontItalicKey) : undefined,
  utilityFontUrl: assetUrl(snapshot.props.utilityFontKey),
  includeAudio: true,
  tracks: snapshot.tracks.map((track) => ({ ...track, audioUrl: assetUrl(track.audioKey) })),
};

const frameRange = [snapshot.tracks.find((track) => track.sequence === 6).startFrame - 180, snapshot.tracks.find((track) => track.sequence === 6).startFrame + 270];
const concurrency = Number(concurrencyArg);
const outputPath = path.resolve(outputDir, `range-c${concurrency}-${finishArg.toLowerCase()}.mp4`);
await fs.mkdir(outputDir, { recursive: true });
const startedAt = performance.now();
const stage = { renderedDoneIn: null, encodedDoneIn: null, stitchStages: [], resolvedConcurrency: null, progressEvents: 0 };

try {
  const bundleStartedAt = performance.now();
  const serveUrl = await bundle({ entryPoint: path.resolve("src/remotion/index.tsx"), rootDir: path.resolve(".") });
  const bundleMs = performance.now() - bundleStartedAt;
  const selectStartedAt = performance.now();
  const composition = await selectComposition({ serveUrl, id: "AlbumLandscape", inputProps: props });
  const selectMs = performance.now() - selectStartedAt;
  const renderStartedAt = performance.now();
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    audioCodec: "aac",
    outputLocation: outputPath,
    inputProps: props,
    frameRange,
    concurrency,
    crf: 23,
    pixelFormat: "yuv420p",
    colorSpace: "bt709",
    hardwareAcceleration: "disable",
    logLevel: "verbose",
    onStart: ({ resolvedConcurrency }) => { stage.resolvedConcurrency = resolvedConcurrency; },
    onProgress: ({ renderedDoneIn, encodedDoneIn, stitchStage }) => {
      stage.renderedDoneIn = renderedDoneIn;
      stage.encodedDoneIn = encodedDoneIn;
      if (stitchStage && !stage.stitchStages.includes(stitchStage)) stage.stitchStages.push(stitchStage);
      stage.progressEvents += 1;
    },
  });
  const stat = await fs.stat(outputPath);
  process.stdout.write(JSON.stringify({ ok: true, concurrency, finish: finishArg, frameRange, frameCount: frameRange[1] - frameRange[0] + 1, durationSeconds: (frameRange[1] - frameRange[0] + 1) / snapshot.expected.fps, bundleMs, selectMs, renderMs: performance.now() - renderStartedAt, wallMs: performance.now() - startedAt, outputPath, outputBytes: stat.size, stage }) + "\n");
} finally {
  await new Promise((resolve) => server.close(resolve));
}
