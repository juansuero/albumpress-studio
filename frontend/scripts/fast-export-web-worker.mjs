import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { bundle } from "@remotion/bundler";
import WebSocket from "ws";

const inputPath = process.argv[2];
if (!inputPath) throw new Error("A Fast Export input JSON path is required.");
const input = JSON.parse(await fsp.readFile(inputPath, "utf8"));
const snapshot = input.snapshot;
const projectRoot = path.resolve(snapshot.projectFolder);
const outputPath = path.resolve(input.videoOnlyPath);
const jobRoot = path.dirname(inputPath);
const chromeProfile = path.join(jobRoot, "chrome-profile");
const assetPaths = new Map();
const token = crypto.randomBytes(16).toString("hex");
let chromeProcess = null;
let server = null;
let cdp = null;
let shuttingDown = false;

function emit(value) {
  process.stdout.write(JSON.stringify(value) + "\n");
}

process.on("uncaughtException", (error) => {
  emit({ stage: "failed", progress: 0, error: error?.stack ?? String(error), message: "Fast Export worker initialization failed." });
  process.exitCode = 1;
});
process.on("unhandledRejection", (error) => {
  emit({ stage: "failed", progress: 0, error: error?.stack ?? String(error), message: "Fast Export worker initialization failed." });
  process.exitCode = 1;
});

function assertInsideProject(relativePath) {
  const candidate = path.resolve(projectRoot, relativePath);
  const relative = path.relative(projectRoot, candidate);
  if (relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) throw new Error("Fast Export asset escapes the Project Folder: " + relativePath);
  return candidate;
}

function hashFile(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    let bytes = 0;
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => { bytes += chunk.length; hash.update(chunk); });
    stream.on("error", reject);
    stream.on("end", () => resolve({ sha256: hash.digest("hex"), bytes }));
  });
}

function readRequestBody(request, target) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    let bytes = 0;
    const output = fs.createWriteStream(target, { flags: "w" });
    request.on("data", (chunk) => { bytes += chunk.length; hash.update(chunk); if (!output.write(chunk)) request.pause(); });
    output.on("drain", () => request.resume());
    request.on("end", () => output.end(() => resolve({ bytes, sha256: hash.digest("hex") })));
    request.on("error", (error) => { output.destroy(); reject(error); });
    output.on("error", reject);
  });
}

function sendJson(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "content-length": Buffer.byteLength(body) });
  response.end(body);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); } catch (error) { reject(error); } });
    request.on("error", reject);
  });
}

async function getFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => { const port = probe.address().port; probe.close(() => resolve(port)); });
  });
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    path.join(process.env.PROGRAMFILES ?? "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
    path.join(process.env["PROGRAMFILES(X86)"] ?? "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
    path.join(process.env.LOCALAPPDATA ?? "", "Google/Chrome/Application/chrome.exe"),
  ].filter(Boolean);
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) throw new Error("An isolated Chrome executable is required for Fast Export.");
  return found;
}

async function waitForJson(url, timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function createCdpClient(url) {
  const socket = new WebSocket(url);
  const pending = new Map();
  let nextId = 1;
  socket.on("message", (data) => {
    const message = JSON.parse(String(data));
    if (!message.id) return;
    const holder = pending.get(message.id);
    if (!holder) return;
    pending.delete(message.id);
    if (message.error) holder.reject(new Error(message.error.message));
    else holder.resolve(message.result);
  });
  const ready = new Promise((resolve, reject) => { socket.once("open", resolve); socket.once("error", reject); });
  return {
    async send(method, params = {}) {
      await ready;
      const id = nextId++;
      const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      socket.send(JSON.stringify({ id, method, params }));
      return result;
    },
    async evaluate(expression) {
      const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "Browser evaluation failed");
      if (result.result?.subtype === "error") throw new Error(result.result.description ?? "Browser evaluation failed");
      return result.result?.value;
    },
    close() { socket.close(); },
  };
}

async function launchChrome(url, port) {
  await fsp.rm(chromeProfile, { recursive: true, force: true });
  await fsp.mkdir(chromeProfile, { recursive: true });
  const args = [
    "--headless=new", "--disable-gpu-sandbox", `--remote-debugging-port=${port}`,
    `--user-data-dir=${chromeProfile}`, "--no-first-run", "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required", "--window-size=1920,1080", url,
  ];
  chromeProcess = spawn(chromePath(), args, { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  chromeProcess.stderr?.on("data", (chunk) => { stderr += String(chunk).slice(-4000); });
  chromeProcess.on("exit", (code, signal) => { if (!shuttingDown && code !== 0) emit({ stage: "browser", message: "Isolated Chrome exited unexpectedly.", code, signal, stderr: stderr.slice(-2000) }); });
  const version = await waitForJson(`http://127.0.0.1:${port}/json/version`);
  const targets = await waitForJson(`http://127.0.0.1:${port}/json`);
  const page = Array.isArray(targets) ? targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl) : null;
  if (!page?.webSocketDebuggerUrl) throw new Error("Isolated Chrome did not expose a page debugging target.");
  cdp = createCdpClient(page.webSocketDebuggerUrl);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Page.navigate", { url });
  const started = Date.now();
  while (Date.now() - started < 30000) {
    try {
      if (await cdp.evaluate("Boolean(window.albumpressFastExport)")) return version;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out waiting for the Fast Export browser entry.");
}

async function cleanup() {
  if (shuttingDown && !chromeProcess && !server) return;
  shuttingDown = true;
  try { cdp?.close(); } catch {}
  cdp = null;
  if (chromeProcess?.pid) {
    if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(chromeProcess.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    else chromeProcess.kill("SIGTERM");
  }
  chromeProcess = null;
  if (server) await new Promise((resolve) => server.close(resolve));
  server = null;
}

process.once("SIGTERM", () => { void cleanup().finally(() => process.exit(143)); });
process.once("SIGINT", () => { void cleanup().finally(() => process.exit(130)); });

for (const [key, asset] of Object.entries(snapshot.assets ?? {})) {
  if (!asset || typeof asset.relativePath !== "string") throw new Error("Malformed Fast Export asset: " + key);
  const assetPath = assertInsideProject(asset.relativePath);
  const stat = await fsp.stat(assetPath);
  if (!stat.isFile()) throw new Error("Fast Export asset is not a file: " + key);
  assetPaths.set(key, assetPath);
}

const props = {
  ...snapshot.props,
  artworkUrl: null,
  textureUrl: null,
  displayFontUrl: null,
  utilityFontUrl: null,
  includeAudio: false,
  tracks: snapshot.tracks.map((track) => ({ ...track, audioUrl: null })),
};

const outputRoot = path.dirname(outputPath);
await fsp.mkdir(outputRoot, { recursive: true });
const port = await getFreePort();
const chromePort = await getFreePort();
const bundleRoot = await bundle({ entryPoint: path.resolve("scripts/fast-export-web-entry.tsx"), rootDir: path.resolve("."), ignoreRegisterRootWarning: true });
const bundleSource = await fsp.readFile(path.join(bundleRoot, "bundle.js"));

server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    const prefix = `/asset/${token}/`;
    if (request.method === "GET" && url.pathname === "/") { response.writeHead(200, { "content-type": "text/html; charset=utf-8" }); response.end("<!doctype html><meta charset=utf-8><title>Fast Export</title><script src=/bundle.js></script>"); return; }
    if (request.method === "GET" && url.pathname === "/bundle.js") { response.writeHead(200, { "content-type": "text/javascript; charset=utf-8" }); response.end(bundleSource); return; }
    if (request.method === "GET" && /^\/[A-Za-z0-9_.-]+\.(?:js|ico)$/.test(url.pathname)) {
      const filePath = path.resolve(bundleRoot, url.pathname.slice(1));
      if (path.dirname(filePath) !== path.resolve(bundleRoot)) { response.writeHead(404); response.end(); return; }
      const stat = await fsp.stat(filePath).catch(() => null);
      if (!stat?.isFile()) { response.writeHead(404); response.end(); return; }
      response.writeHead(200, { "content-type": url.pathname.endsWith(".ico") ? "image/x-icon" : "text/javascript; charset=utf-8", "content-length": stat.size });
      fs.createReadStream(filePath).pipe(response);
      return;
    }
    if (request.method === "GET" && url.pathname === "/props.json") { sendJson(response, 200, { inputProps: props, durationInFrames: snapshot.expected.frameCount, fps: snapshot.expected.fps, width: snapshot.expected.width, height: snapshot.expected.height }); return; }
    if (request.method === "GET" && url.pathname.startsWith(prefix)) {
      const key = decodeURIComponent(url.pathname.slice(prefix.length));
      const filePath = assetPaths.get(key);
      if (!filePath) { response.writeHead(404); response.end(); return; }
      const stat = await fsp.stat(filePath);
      let start = 0; let end = stat.size - 1; let status = 200;
      const range = request.headers.range;
      if (range) {
        const match = /^bytes=(\d*)-(\d*)$/.exec(range);
        if (!match) { response.writeHead(416, { "Content-Range": `bytes */${stat.size}` }); response.end(); return; }
        if (match[1]) start = Number(match[1]);
        if (match[2]) end = Number(match[2]);
        if (!match[1] && match[2]) start = Math.max(0, stat.size - Number(match[2]));
        end = Math.min(end, stat.size - 1);
        if (start < 0 || end < start || start >= stat.size) { response.writeHead(416, { "Content-Range": `bytes */${stat.size}` }); response.end(); return; }
        status = 206;
      }
      const ext = path.extname(filePath).toLowerCase();
      const mime = ext === ".png" ? "image/png" : ext === ".ttf" ? "font/ttf" : ext === ".woff2" ? "font/woff2" : "application/octet-stream";
      const headers = { "Access-Control-Allow-Origin": "*", "Accept-Ranges": "bytes", "Content-Length": String(end - start + 1), "Content-Type": mime };
      if (status === 206) headers["Content-Range"] = `bytes ${start}-${end}/${stat.size}`;
      response.writeHead(status, headers);
      fs.createReadStream(filePath, { start, end }).pipe(response);
      return;
    }
    if (request.method === "POST" && url.pathname === "/event") { const value = await readJson(request); emit(value); sendJson(response, 200, { ok: true }); return; }
    if (request.method === "POST" && url.pathname === "/transfer") { const transfer = await readRequestBody(request, outputPath); sendJson(response, 200, { ...transfer, path: outputPath }); return; }
    response.writeHead(404); response.end();
  } catch (error) { sendJson(response, 500, { error: error instanceof Error ? error.message : String(error) }); }
});

await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
for (const key of ["artwork", "texture", "displayFont", "utilityFont"]) props[`${key}Url`] = `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(key)}`;
if (props.brand?.enabled) {
  props.brand.monogramUrl = `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(props.brand.monogramKey)}`;
  props.brand.lockupUrl = `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(props.brand.lockupKey)}`;
  props.brand.watermarkUrl = `http://127.0.0.1:${port}/asset/${token}/${encodeURIComponent(props.brand.watermarkKey)}`;
}
for (const track of props.tracks) track.audioUrl = null;

try {
  emit({ stage: "preflight", progress: 0.01, message: "Starting isolated Fast Export worker." });
  const version = await launchChrome(`http://127.0.0.1:${port}/`, chromePort);
  const systemInfo = await cdp.send("SystemInfo.getInfo").catch(() => null);
  const capability = await cdp.evaluate("window.albumpressFastExport.capability()" );
  emit({ stage: "preflight", progress: 0.08, capability, browser: { version, systemInfo }, message: "Fast Export capability checked." });
  if (!capability?.selectedOutputTarget) throw new Error(JSON.stringify({ code: "FAST_EXPORT_UNAVAILABLE", capability }));
  const evidence = await cdp.evaluate("window.albumpressFastExport.render()" );
  emit({ stage: "video-transferred", progress: 0.72, evidence, browser: { version, systemInfo }, message: "Web Renderer video transferred to staging." });
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  emit({ stage: "failed", progress: 0, error: message, message: message.includes("FAST_EXPORT_UNAVAILABLE") ? "Fast Export is unavailable in this browser." : "Fast Export worker failed." });
  process.exitCode = 1;
} finally {
  await cleanup();
}
