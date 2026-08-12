import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";

const inputPath = process.argv[2];
if (!inputPath) throw new Error("A thumbnail candidate input JSON path is required.");
const input = JSON.parse(await fs.readFile(inputPath, "utf8"));
const snapshot = input.snapshot;
const projectRoot = path.resolve(snapshot.projectFolder);
const outputDirectory = path.resolve(input.outputDirectory);
const renderFrame = Number.isFinite(Number(input.frame)) ? Math.max(0, Number(input.frame)) : 0;
const variants = input.variants;
if (!Array.isArray(variants) || variants.length !== 3 || variants.map((item) => item.slot).join("") !== "ABC") {
  throw new Error("Thumbnail candidates must contain ordered A/B/C variants.");
}
const token = crypto.randomBytes(16).toString("hex");
const assetPaths = new Map();
const mimeTypes = { artwork: "image/png", texture: "image/png", displayFont: "font/ttf", utilityFont: "font/woff2", "brand-monogram": "image/png", "brand-lockup": "image/png", "brand-watermark": "image/png", "brand-vector": "image/svg+xml", "brand-approvalManifest": "application/json" };

function assertInside(relativePath) {
  const candidate = path.resolve(projectRoot, relativePath);
  const relative = path.relative(projectRoot, candidate);
  if (relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) throw new Error("Thumbnail asset escapes the Project Folder.");
  return candidate;
}

for (const [key, asset] of Object.entries(snapshot.assets)) {
  if (asset?.relativePath) {
    const filePath = assertInside(asset.relativePath);
    const stat = await fs.stat(filePath);
    if (!stat.isFile()) throw new Error("Thumbnail asset is not a file: " + key);
    assetPaths.set(key, filePath);
  }
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
    response.writeHead(200, { "Access-Control-Allow-Origin": "*", "Content-Length": stat.size, "Content-Type": mimeTypes[key] ?? "application/octet-stream" });
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    const handle = await fs.open(filePath, "r");
    const stream = handle.createReadStream();
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

await fs.mkdir(outputDirectory, { recursive: true });
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
  thumbnailMode: true,
} : snapshot.props.brand;
const baseProps = {
  ...snapshot.props,
  artworkUrl: assetUrl(snapshot.props.artworkKey),
  displayFontUrl: assetUrl(snapshot.props.displayFontKey),
  displayFontItalicUrl: snapshot.props.displayFontItalicKey ? assetUrl(snapshot.props.displayFontItalicKey) : undefined,
  utilityFontUrl: assetUrl(snapshot.props.utilityFontKey),
  textureUrl: snapshot.props.textureKey ? assetUrl(snapshot.props.textureKey) : undefined,
  includeAudio: false,
  brand,
  tracks: snapshot.tracks,
};

try {
  emit({ stage: "bundling", progress: 0 });
  const serveUrl = await bundle({ entryPoint: path.resolve("src/remotion/index.tsx"), rootDir: path.resolve(".") });
  for (let index = 0; index < variants.length; index += 1) {
    const variant = variants[index];
    const inputProps = { ...baseProps, thumbnailEditorial: variant.override };
    const composition = await selectComposition({ serveUrl, id: "AlbumLandscape", inputProps });
    const output = path.join(outputDirectory, `${variant.slot}.png`);
    const rawOutput = output + ".raw.png";
    emit({ stage: "rendering", slot: variant.slot, progress: 0.2 + index * 0.25 });
    await renderStill({ composition, serveUrl, frame: renderFrame, output: rawOutput, inputProps, scale: 2 / 3 });
    await fs.rename(rawOutput, output);
  }
  emit({ stage: "complete", progress: 1 });
} finally {
  await new Promise((resolve) => server.close(resolve));
}
