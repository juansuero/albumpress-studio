import React from "react";
import {
  canRenderMediaOnWeb,
  getEncodableAudioCodecs,
  getEncodableVideoCodecs,
  renderMediaOnWeb,
} from "@remotion/web-renderer";
import { AlbumLandscape } from "../src/remotion/AlbumLandscape";

type FastExportPayload = {
  inputProps: Parameters<typeof AlbumLandscape>[0];
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
};

type Progress = {
  progress?: number;
  renderedFrames?: number;
  encodedFrames?: number;
  doneIn?: number | null;
  renderEstimatedTime?: number;
};

type FastExportWindow = Window & {
  albumpressFastExport?: {
    capability: () => Promise<Record<string, unknown>>;
    render: () => Promise<Record<string, unknown>>;
    cancel: () => Promise<Record<string, unknown>>;
  };
};

const payloadPromise = fetch("/props.json").then((response) => response.json() as Promise<FastExportPayload>);
let activeController: AbortController | null = null;
let lastEventAt = 0;

async function postEvent(value: Record<string, unknown>) {
  try {
    await fetch("/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(value),
    });
  } catch {
    // The backend owns the durable status; a lost progress event is non-fatal.
  }
}

function capabilityOptions(payload: FastExportPayload) {
  return {
    container: "mp4" as const,
    videoCodec: "h264" as const,
    audioCodec: null,
    muted: true,
    width: payload.width,
    height: payload.height,
  };
}

async function gpuAdapterInfo() {
  const gpu = (navigator as Navigator & { gpu?: { requestAdapter: () => Promise<{ name?: string; info?: Record<string, unknown> } | null> } }).gpu;
  if (!gpu) return { available: false, reason: "navigator.gpu unavailable" };
  try {
    const timeout = new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 1500));
    const adapter = await Promise.race([gpu.requestAdapter(), timeout]);
    if (!adapter) return { available: false, reason: "No WebGPU adapter reported" };
    const details = adapter as typeof adapter & { name?: string; info?: Record<string, unknown> };
    return { available: true, name: details.name ?? null, info: details.info ?? null };
  } catch (error) {
    return { available: false, reason: error instanceof Error ? error.message : String(error) };
  }
}

async function capability() {
  const payload = await payloadPromise;
  const options = capabilityOptions(payload);
  const [arrayBuffer, webFs, videoCodecs, audioCodecs, gpu] = await Promise.all([
    canRenderMediaOnWeb({ ...options, outputTarget: "arraybuffer" }),
    canRenderMediaOnWeb({ ...options, outputTarget: "web-fs" }),
    getEncodableVideoCodecs("mp4"),
    getEncodableAudioCodecs("mp4"),
    gpuAdapterInfo(),
  ]);
  const result = {
    checkedAt: new Date().toISOString(),
    browser: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
      crossOriginIsolated: window.crossOriginIsolated,
      webCodecs: {
        videoEncoder: typeof VideoEncoder !== "undefined",
        audioEncoder: typeof AudioEncoder !== "undefined",
        videoCodecs,
        audioCodecs,
      },
      gpu,
    },
    arrayBuffer,
    webFs,
    selectedOutputTarget: webFs.canRender ? "web-fs" : arrayBuffer.canRender ? "arraybuffer" : null,
    requestedHardwareAcceleration: "prefer-hardware",
    includeAudio: false,
    muted: true,
  };
  await postEvent({ stage: "preflight", progress: 0.04, capability: result, message: "Checking browser video capability." });
  return result;
}

async function render() {
  const payload = await payloadPromise;
  const checked = await capability();
  const selectedOutputTarget = checked.selectedOutputTarget as "web-fs" | "arraybuffer" | null;
  if (!selectedOutputTarget) {
    throw new Error(JSON.stringify({ code: "FAST_EXPORT_UNAVAILABLE", capability: checked }));
  }
  const controller = new AbortController();
  activeController = controller;
  const startedAt = performance.now();
  let latestProgress: Progress = {};
  const inputProps = { ...payload.inputProps, includeAudio: false };
  const result = await renderMediaOnWeb({
    composition: {
      id: "AlbumLandscape",
      component: AlbumLandscape,
      width: payload.width,
      height: payload.height,
      fps: payload.fps,
      durationInFrames: payload.durationInFrames,
      defaultProps: inputProps,
    },
    inputProps,
    frameRange: null,
    muted: true,
    audioCodec: null,
    videoCodec: "h264",
    container: "mp4",
    outputTarget: selectedOutputTarget,
    hardwareAcceleration: "prefer-hardware",
    logLevel: "verbose",
    signal: controller.signal,
    onProgress: (progress) => {
      latestProgress = progress;
      const now = performance.now();
      if (now - lastEventAt > 250 || progress.progress === 1) {
        lastEventAt = now;
        void postEvent({ stage: "video-rendering", progress: 0.08 + Math.max(0, Math.min(1, Number(progress.progress) || 0)) * 0.55, renderer: progress, message: "Rendering video frames." });
      }
    },
  });
  const blob = await result.getBlob();
  await postEvent({ stage: "transfer", progress: 0.7, transferStartedAt: new Date().toISOString(), bytes: blob.size, outputTarget: selectedOutputTarget, message: "Transferring video to durable staging." });
  const transferStarted = performance.now();
  const transfer = await fetch("/transfer", {
    method: "POST",
    headers: { "content-type": "video/mp4" },
    body: blob,
  });
  if (!transfer.ok) throw new Error(`Video transfer failed: ${transfer.status}`);
  const transferResult = await transfer.json() as Record<string, unknown>;
  activeController = null;
  const elapsedMs = Math.round(performance.now() - startedAt);
  const transferMs = Math.round(performance.now() - transferStarted);
  const evidence = {
    elapsedMs,
    transferMs,
    outputTarget: selectedOutputTarget,
    includeAudio: false,
    muted: true,
    hardwareAcceleration: "prefer-hardware",
    bytes: blob.size,
    renderedFrames: latestProgress.renderedFrames ?? null,
    encodedFrames: latestProgress.encodedFrames ?? null,
    renderEstimatedTime: latestProgress.renderEstimatedTime ?? null,
    transfer: transferResult,
  };
  await postEvent({ stage: "video-transferred", progress: 0.72, evidence, message: "Video staged; starting audio assembly." });
  return evidence;
}

async function cancel() {
  if (activeController) activeController.abort();
  return { cancelled: true };
}

(window as FastExportWindow).albumpressFastExport = { capability, render, cancel };
