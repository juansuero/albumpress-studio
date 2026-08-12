from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .json_store import atomic_write_json, read_json
from .project_artifact_library import ArtifactLibraryError, ProjectArtifactLibrary
from .projects import ProjectError, normalized_path, utc_now
from .brand import brand_input_props, brand_snapshot_assets
from .video import build_video_state
from .video_render import (
    RENDER_CONCURRENCY,
    VideoRenderError,
    _active_job,
    _inside,
    _job_dir,
    _node_path,
    _read_status,
    _sha256,
    _status_path,
    _validate_snapshot_current,
    _write_status,
    _write_tone,
)


FAST_TICKET_ID = "ticket-20-fast"
FAST_NODE_SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "fast-export-web-worker.mjs"
FAST_ENTRY_SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "fast-export-web-entry.tsx"
FAST_FPS = 30
FAST_WIDTH = 1920
FAST_HEIGHT = 1080
FAST_TRACK_SECONDS = 5
FAST_TRACK_COUNT = 2
FAST_TOTAL_SECONDS = FAST_TRACK_SECONDS * FAST_TRACK_COUNT
AAC_PACKET_SECONDS = 1024 / 48000


def _fast_code_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in (FAST_NODE_SCRIPT, FAST_ENTRY_SCRIPT, Path(__file__)):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _asset_path(project_root: Path, config: dict[str, Any], key: str, fallback: str | None = None) -> Path:
    assets = config.get("assets") if isinstance(config.get("assets"), dict) else {}
    record = assets.get(key) or (assets.get(fallback) if fallback else None)
    if not isinstance(record, dict) or not record.get("path"):
        raise VideoRenderError(f"Prepared Video asset {key} is not registered.")
    path = _inside(project_root, project_root / str(record["path"]))
    if not path.is_file():
        raise VideoRenderError(f"Prepared Video asset {key} is missing.")
    return path


def _asset_records(project_root: Path, config: dict[str, Any], keys: tuple[tuple[str, str, str | None], ...]) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    assets: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for key, config_key, fallback in keys:
        path = _asset_path(project_root, config, config_key, fallback)
        paths[key] = path
        assets[key] = {"relativePath": path.relative_to(project_root).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}
    return assets, paths


def _timeline_props(config: dict[str, Any], tracks: list[dict[str, Any]]) -> dict[str, Any]:
    props = {
        "artist": str(config.get("artist", "")),
        "album": str(config.get("album", "")),
        "displayFontFamily": config["typography"]["displayFontFamily"],
        "utilityFontFamily": config["typography"]["utilityFontFamily"],
        "colors": config["colors"],
        "cinematicFinish": config.get("cinematicFinish", "Subtle"),
        "reducedMotion": bool(config.get("reducedMotion", False)),
        "artworkKey": "artwork",
        "textureKey": "texture",
        "displayFontKey": "displayFont",
        "utilityFontKey": "utilityFont",
        "fadeInSeconds": 1,
        "fadeOutSeconds": 2,
        "tracks": tracks,
        "includeAudio": False,
        "brand": brand_input_props(config.get("brand") or {}),
    }
    if isinstance(config.get("assets"), dict) and "displayFontItalic" in config["assets"]:
        props["displayFontItalicKey"] = "displayFontItalic"
    return props


def _fast_snapshot(project_root: Path, job_id: str, *, synthetic: bool) -> dict[str, Any]:
    state = build_video_state(project_root / "project.json")
    if not state["ready"]:
        raise VideoRenderError(("Fast Export is blocked: " if synthetic else "Real Fast Export is blocked: ") + "; ".join(state["issues"]))
    config = state["config"]
    asset_keys = [("artwork", "effectiveArtwork", "artwork"), ("texture", "texture", None), ("displayFont", "displayFont", None), ("utilityFont", "utilityFont", None)]
    if isinstance(config.get("assets"), dict) and "displayFontItalic" in config["assets"]:
        asset_keys.insert(3, ("displayFontItalic", "displayFontItalic", None))
    assets, _ = _asset_records(
        project_root,
        config,
        tuple(asset_keys),
    )
    assets.update(brand_snapshot_assets(project_root, config.get("brand") or {}))
    tracks: list[dict[str, Any]] = []
    selection_snapshot: list[dict[str, Any]] = []
    if synthetic:
        synthetic_dir = _job_dir(project_root, job_id) / "synthetic"
        synthetic_dir.mkdir(parents=True, exist_ok=True)
        for sequence, frequency in ((1, 440), (2, 660)):
            path = synthetic_dir / f"audio-{sequence:02d}.wav"
            _write_tone(path, frequency, FAST_TRACK_SECONDS)
            key = f"audio-{sequence}"
            assets[key] = {"relativePath": path.relative_to(project_root).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}
            tracks.append({
                "trackId": f"synthetic-fast-{sequence:02d}",
                "sequence": sequence,
                "title": f"Synthetic Fast Boundary {sequence}",
                "durationSeconds": FAST_TRACK_SECONDS,
                "startFrame": (sequence - 1) * FAST_TRACK_SECONDS * FAST_FPS,
                "durationInFrames": FAST_TRACK_SECONDS * FAST_FPS,
                "audioKey": key,
            })
    else:
        for item in sorted(state["composition"]["timeline"], key=lambda value: int(value["sequence"])):
            final_path = str(item["finalPath"])
            audio_path = _inside(project_root, project_root / final_path)
            if not final_path.replace("\\", "/").startswith("final/") or not audio_path.is_file():
                raise VideoRenderError(f"Track {item['sequence']} does not resolve to a current Final Instrumental.")
            audio_key = f"audio-{int(item['sequence'])}"
            assets[audio_key] = {"relativePath": audio_path.relative_to(project_root).as_posix(), "sha256": _sha256(audio_path), "bytes": audio_path.stat().st_size}
            tracks.append({
                "trackId": item["trackId"],
                "sequence": int(item["sequence"]),
                "title": item["title"],
                "durationSeconds": float(item["durationSeconds"]),
                "startFrame": int(item["startFrame"]),
                "durationInFrames": int(item["durationInFrames"]),
                "outputId": item["outputId"],
                "fileFingerprint": item.get("fileFingerprint"),
                "finalPath": final_path,
                "audioKey": audio_key,
            })
            selection_snapshot.append({"trackId": item["trackId"], "outputId": item["outputId"], "fileFingerprint": item.get("fileFingerprint"), "finalPath": final_path})
    frame_count = sum(int(track["durationInFrames"]) for track in tracks)
    return {
        "snapshotVersion": 2,
        "kind": "synthetic-fast-export" if synthetic else "real-fast-album",
        "mode": "fast",
        "projectFolder": str(project_root),
        "projectManifest": "project.json",
        "configuration": "video/config.json",
        "codeFingerprint": __import__("app.video_render", fromlist=["_code_fingerprint"])._code_fingerprint(),
        "fastCodeFingerprint": _fast_code_fingerprint(),
        "fingerprints": {"projectManifest": _sha256(project_root / "project.json"), "configuration": _sha256(project_root / "video" / "config.json")},
        "assets": assets,
        "tracks": tracks,
        "selectionSnapshot": selection_snapshot,
        "expected": {"width": FAST_WIDTH, "height": FAST_HEIGHT, "fps": FAST_FPS, "frameCount": frame_count, "durationSeconds": frame_count / FAST_FPS, "videoCodec": "h264", "audioCodec": "aac", "audioSampleRate": 48000, "audioChannels": 2},
        "settings": {"codec": "h264", "audioCodec": "aac", "audioBitrate": 192000, "pixelFormat": "yuv420p", "colorSpace": "bt709", "concurrency": 1, "hardwareAcceleration": "prefer-hardware", "outputTarget": "web-fs-when-supported", "includeAudio": False, "audioTransform": "frame-authoritative FFmpeg AAC-LC"},
        "props": _timeline_props(config, tracks),
    }


def _validate_fast_snapshot_current(project_root: Path, snapshot: dict[str, Any]) -> None:
    _validate_snapshot_current(project_root, snapshot)
    if snapshot.get("fastCodeFingerprint") != _fast_code_fingerprint():
        raise VideoRenderError("Fast Export snapshot is stale: the Fast worker changed before promotion.")


def _ffmpeg_path() -> str:
    path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not path:
        raise VideoRenderError("FFmpeg is required for Fast Export audio assembly and muxing.")
    return path


def _ffprobe_path() -> str:
    path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if not path:
        raise VideoRenderError("FFprobe is required for Fast Export validation.")
    return path


def _run_child(command: list[str], record: dict[str, Any], *, label: str) -> tuple[int, str]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    except OSError as exc:
        raise VideoRenderError(f"Fast Export {label} could not start: {exc}; executable={command[0]!r}") from exc
    record["childProcess"] = child
    stderr = ""
    try:
        while True:
            if record.get("cancelRequested") and child.poll() is None:
                child.terminate()
            code = child.poll()
            if code is not None:
                stderr = child.stderr.read() if child.stderr else ""
                return code, stderr
            time.sleep(0.05)
    finally:
        record["childProcess"] = None


def _audio_plan(snapshot: dict[str, Any], project_root: Path, output_path: Path) -> dict[str, Any]:
    tracks = sorted(snapshot["tracks"], key=lambda item: int(item["sequence"]))
    filters: list[str] = []
    inputs: list[str] = []
    for index, track in enumerate(tracks):
        duration = int(track["durationInFrames"]) / int(snapshot["expected"]["fps"])
        filters.append(f"[{index}:a]atrim=start=0:end={duration:.6f},asetpts=PTS-STARTPTS[a{index}]")
        inputs.append(str(_inside(project_root, project_root / snapshot["assets"][track["audioKey"]]["relativePath"])))
    labels = "".join(f"[a{index}]" for index in range(len(tracks)))
    total = int(snapshot["expected"]["frameCount"]) / int(snapshot["expected"]["fps"])
    fade_out = max(0, total - 2)
    filters.append(f"{labels}concat=n={len(tracks)}:v=0:a=1[joined]")
    filters.append(f"[joined]aresample=48000:async=0,aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:st=0:d=1,afade=t=out:st={fade_out:.6f}:d=2[aout]")
    return {"inputs": inputs, "filterGraph": ";".join(filters), "frameCount": int(snapshot["expected"]["frameCount"]), "fps": int(snapshot["expected"]["fps"]), "totalSeconds": total, "fadeInSeconds": 1, "fadeOutSeconds": 2, "boundaryFrames": [int(track["startFrame"]) for track in tracks[1:]], "outputPath": str(output_path)}


def _assemble_audio(snapshot: dict[str, Any], project_root: Path, audio_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    plan = _audio_plan(snapshot, project_root, audio_path)
    args = [_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    for input_path in plan["inputs"]:
        args.extend(["-i", input_path])
    args.extend(["-filter_complex", plan["filterGraph"], "-map", "[aout]", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(audio_path)])
    plan["command"] = args
    atomic_write_json(audio_path.with_suffix(".plan.json"), plan)
    code, stderr = _run_child(args, record, label="audio assembly")
    if record.get("cancelRequested"):
        raise VideoRenderError("Fast Export audio assembly was cancelled.")
    if code != 0:
        raise VideoRenderError(f"Fast Export audio assembly failed: {stderr[-3000:]}")
    return plan


def _mux_video(video_path: Path, audio_path: Path, output_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    args = [_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart", str(output_path)]
    code, stderr = _run_child(args, record, label="video mux")
    if record.get("cancelRequested"):
        raise VideoRenderError("Fast Export mux was cancelled.")
    if code != 0:
        raise VideoRenderError(f"Fast Export mux failed: {stderr[-3000:]}")
    return {"command": args, "videoCopied": True, "audioCopied": True}


def _ffprobe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    command = [_ffprobe_path(), "-v", "error"]
    if count_frames:
        command.append("-count_frames")
    command.extend(["-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,color_space,color_primaries,color_trc,sample_rate,channels,nb_read_frames,start_time,time_base", "-of", "json", str(path)])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoRenderError((completed.stderr or "FFprobe could not read Fast Export media.").strip())
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise VideoRenderError("FFprobe returned an invalid Fast Export payload.")
    return value


def _packet_timestamps(path: Path, stream: str) -> dict[str, Any]:
    ffprobe = _ffprobe_path()
    completed = subprocess.run([ffprobe, "-v", "error", "-select_streams", stream, "-show_entries", "packet=pts_time,dts_time", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoRenderError("FFprobe could not inspect Fast Export timestamps.")
    packets = json.loads(completed.stdout).get("packets", [])
    values = [float(packet["pts_time"]) for packet in packets if packet.get("pts_time") not in {None, "N/A"}]
    return {"count": len(values), "monotonic": all(current >= previous for previous, current in zip(values, values[1:])), "first": values[0] if values else None, "last": values[-1] if values else None}


def _h264_elementary(path: Path, output_path: Path) -> str:
    ffmpeg = _ffmpeg_path()
    completed = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-c:v", "copy", "-bsf:v", "h264_mp4toannexb", "-f", "h264", str(output_path)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoRenderError((completed.stderr or "Could not extract H.264 elementary stream.").strip())
    return _sha256(output_path)


def _decode_mono_samples(path: Path, *, start: float, duration: float) -> list[int]:
    completed = subprocess.run([_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, start):.6f}", "-t", f"{duration:.6f}", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"], capture_output=True, check=False)
    if completed.returncode != 0:
        raise VideoRenderError("Could not decode the Fast Export audio boundary.")
    return [int.from_bytes(completed.stdout[index:index + 2], "little", signed=True) for index in range(0, len(completed.stdout) - 1, 2)]


def _rms(samples: list[int]) -> float:
    return math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples)))


def _audio_continuity(path: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    tracks = sorted(snapshot["tracks"], key=lambda item: int(item["sequence"]))
    project_root = Path(snapshot["projectFolder"])
    boundaries = [int(track["startFrame"]) / int(snapshot["expected"]["fps"]) for track in tracks[1:]]
    metrics = []
    for index, boundary in enumerate(boundaries, start=1):
        start = max(0, boundary - 0.2)
        duration = 0.4
        samples = _decode_mono_samples(path, start=start, duration=duration)
        split = min(len(samples), max(0, int((boundary - start) * 48000)))
        before = samples[:split]
        after = samples[split:]
        rms_before = _rms(before)
        rms_after = _rms(after)
        discontinuity = abs(samples[split] - samples[split - 1]) if 0 < split < len(samples) else 0
        previous = tracks[index - 1]
        current = tracks[index]
        previous_path = _inside(project_root, project_root / snapshot["assets"][previous["audioKey"]]["relativePath"])
        current_path = _inside(project_root, project_root / snapshot["assets"][current["audioKey"]]["relativePath"])
        source_before = _decode_mono_samples(previous_path, start=max(0, int(previous["durationInFrames"]) / int(snapshot["expected"]["fps"]) - 0.2), duration=0.2)
        source_after = _decode_mono_samples(current_path, start=0, duration=0.2)
        source_rms_before = _rms(source_before)
        source_rms_after = _rms(source_after)
        output_silence_before = rms_before < 8
        output_silence_after = rms_after < 8
        source_silence_before = source_rms_before < 8
        source_silence_after = source_rms_after < 8
        source_mismatch = output_silence_before != source_silence_before or output_silence_after != source_silence_after
        expected_samples = int(round(duration * 48000))
        metrics.append({
            "boundarySeconds": boundary,
            "rmsBefore": round(rms_before, 2),
            "rmsAfter": round(rms_after, 2),
            "sourceRmsBefore": round(source_rms_before, 2),
            "sourceRmsAfter": round(source_rms_after, 2),
            "sampleDiscontinuity": discontinuity,
            "decodedSamples": len(samples),
            "expectedSamples": expected_samples,
            "windowComplete": len(samples) >= expected_samples,
            "sourceSilenceExpectedBefore": source_silence_before,
            "sourceSilenceExpectedAfter": source_silence_after,
            "silenceDetected": output_silence_before and not source_silence_before or output_silence_after and not source_silence_after,
            "sourceContentMismatch": source_mismatch,
        })
    return {"boundaries": metrics, "continuous": all(item["windowComplete"] and not item["sourceContentMismatch"] and item["sampleDiscontinuity"] < 30000 for item in metrics)}


def _validate_fast_media(video_only: Path, muxed: Path, snapshot: dict[str, Any], plan: dict[str, Any], staging_dir: Path) -> dict[str, Any]:
    metadata = _ffprobe(muxed, count_frames=True)
    streams = metadata.get("streams") if isinstance(metadata.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    expected = snapshot["expected"]
    duration = float(metadata.get("format", {}).get("duration", 0))
    frame_count = int(video.get("nb_read_frames", 0) or 0)
    tolerance = max(1 / expected["fps"], AAC_PACKET_SECONDS)
    video_timestamps = _packet_timestamps(muxed, "v:0")
    audio_timestamps = _packet_timestamps(muxed, "a:0")
    video_stream_hash = _h264_elementary(video_only, staging_dir / "video-before.h264")
    muxed_stream_hash = _h264_elementary(muxed, staging_dir / "video-after.h264")
    continuity = _audio_continuity(muxed, snapshot)
    checks = {
        "videoStream": bool(video),
        "audioStream": bool(audio),
        "dimensions": video.get("width") == expected["width"] and video.get("height") == expected["height"],
        "cfr30": str(video.get("r_frame_rate")) == f"{expected['fps']}/1" and str(video.get("avg_frame_rate")) == f"{expected['fps']}/1",
        "h264": video.get("codec_name") == "h264",
        "pixelFormat": video.get("pix_fmt") == "yuv420p",
        "colorSpace": video.get("color_space") == "bt709" or video.get("color_primaries") == "bt709",
        "aacLc": audio.get("codec_name") == "aac" and audio.get("profile") in {None, "LC"},
        "sampleRate": int(audio.get("sample_rate", 0) or 0) == 48000,
        "stereo": int(audio.get("channels", 0) or 0) == 2,
        "frameCount": frame_count == int(expected["frameCount"]),
        "duration": abs(duration - float(expected["durationSeconds"])) <= tolerance,
        "videoTimestampsMonotonic": video_timestamps["monotonic"],
        "audioTimestampsMonotonic": audio_timestamps["monotonic"],
        "h264StreamCopy": video_stream_hash == muxed_stream_hash,
        "audioContinuity": bool(continuity["continuous"]),
    }
    if not all(checks.values()):
        raise VideoRenderError("Fast Export failed technical validation: " + ", ".join(key for key, value in checks.items() if not value))
    return {"checks": checks, "ffprobe": metadata, "timestamps": {"video": video_timestamps, "audio": audio_timestamps}, "audioContinuity": continuity, "videoElementarySha256": video_stream_hash, "muxedVideoElementarySha256": muxed_stream_hash, "sha256": _sha256(muxed), "bytes": muxed.stat().st_size, "frameCount": frame_count, "durationSeconds": duration}


def _promote_fast(project_root: Path, job_id: str, muxed_path: Path, snapshot: dict[str, Any], validation: dict[str, Any], plan: dict[str, Any], capability: dict[str, Any] | None, metrics: dict[str, Any]) -> tuple[Path, Path]:
    project = read_json(project_root / "project.json", {})
    artifact_library = project.get("artifactLibrary") if isinstance(project, dict) and isinstance(project.get("artifactLibrary"), dict) else {}
    if int(artifact_library.get("layoutVersion", 0) or 0) >= 1:
        try:
            return ProjectArtifactLibrary(project_root).promote_validated_render(job_id=job_id, snapshot=snapshot, validation=validation, staging_path=muxed_path, ticket_id=FAST_TICKET_ID, output_filename="album-video-fast.mp4")
        except ArtifactLibraryError as exc:
            raise VideoRenderError(str(exc)) from exc
    job_dir = _job_dir(project_root, job_id)
    promotion = job_dir / "promotion"
    promotion.mkdir(parents=True, exist_ok=False)
    output_name = "album-video-fast.mp4"
    shutil.move(str(muxed_path), str(promotion / output_name))
    base = project_root / "video" / "renders" / FAST_TICKET_ID
    base.mkdir(parents=True, exist_ok=True)
    version = 1
    while (base / f"v{version}").exists():
        version += 1
    destination = base / f"v{version}"
    output_relative = (destination / output_name).relative_to(project_root).as_posix()
    render_manifest = {
        "schemaVersion": 1,
        "jobId": job_id,
        "kind": snapshot["kind"],
        "mode": "fast",
        "outputPath": output_relative,
        "snapshot": snapshot,
        "capability": capability,
        "audioPlan": plan,
        "validation": validation,
        "metrics": metrics,
        "promotedAt": utc_now(),
    }
    atomic_write_json(promotion / "render-manifest.json", render_manifest)
    os.replace(promotion, destination)
    return destination / output_name, destination / "render-manifest.json"


def _telemetry_loop(record: dict[str, Any]) -> None:
    try:
        import psutil
    except ImportError:
        record["telemetry"] = {"available": False}
        return
    record["telemetry"] = {"available": True, "peakRssBytes": 0, "samples": 0}
    while not record["telemetryStop"].is_set():
        try:
            root = psutil.Process(record["process"].pid)
            processes = [root, *root.children(recursive=True)]
            rss = sum(item.memory_info().rss for item in processes if item.is_running())
            record["telemetry"]["peakRssBytes"] = max(int(record["telemetry"].get("peakRssBytes", 0)), rss)
            record["telemetry"]["samples"] += 1
        except (psutil.Error, OSError):
            pass
        time.sleep(0.2)


def _cleanup_unpromoted(job_dir: Path) -> None:
    for name in ("staging", "promotion"):
        shutil.rmtree(job_dir / name, ignore_errors=True)


def start_fast_render(project_manifest: str | Path, processes: dict[str, dict[str, Any]], *, synthetic: bool) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    project_root = manifest_path.parent
    if not synthetic:
        from .video_proof import VideoProofError, require_approved_proof

        try:
            require_approved_proof(project_root)
        except VideoProofError as exc:
            raise VideoRenderError(str(exc)) from exc
    active = _active_job(project_root)
    if active:
        raise VideoRenderError(f"Video render job {active} is already active.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(project_root, job_id)
    job_dir.mkdir(parents=True, exist_ok=False)
    snapshot = _fast_snapshot(project_root, job_id, synthetic=synthetic)
    input_path = job_dir / "input.json"
    staging = job_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    video_only_path = staging / "video-only.mp4"
    atomic_write_json(input_path, {"jobId": job_id, "projectFolder": str(project_root), "snapshot": snapshot, "videoOnlyPath": str(video_only_path), "sourceKind": "synthetic" if synthetic else "real", "mode": "fast", "ticketId": FAST_TICKET_ID})
    status_path = job_dir / "status.json"
    status = _write_status(status_path, {"jobId": job_id, "kind": snapshot["kind"], "sourceKind": "synthetic" if synthetic else "real", "mode": "fast", "status": "queued", "stage": "queued", "progress": 0, "message": "Fast Export queued.", "concurrency": 1, "inputPath": str(input_path), "stagingPath": str(video_only_path), "promotedPath": None, "snapshot": snapshot})
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen([_node_path(), str(FAST_NODE_SCRIPT), str(input_path)], cwd=str(FAST_NODE_SCRIPT.parent.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    except OSError as exc:
        _write_status(status_path, {**status, "status": "failed", "stage": "failed", "message": str(exc), "error": str(exc)})
        raise VideoRenderError("The Fast Export worker could not start.") from exc
    record: dict[str, Any] = {"process": process, "statusPath": status_path, "cancelRequested": False, "childProcess": None, "telemetryStop": threading.Event()}
    processes[job_id] = record
    threading.Thread(target=_telemetry_loop, args=(record,), daemon=True).start()
    threading.Thread(target=_monitor_fast, args=(project_root, job_id, processes), daemon=True).start()
    return _read_status(status_path)


def _monitor_fast(project_root: Path, job_id: str, processes: dict[str, dict[str, Any]]) -> None:
    record = processes[job_id]
    status_path = record["statusPath"]
    job_dir = _job_dir(project_root, job_id)
    raw_worker_output: list[str] = []
    try:
        _write_status(status_path, {**_read_status(status_path), "status": "running", "stage": "preflight", "progress": 0.01, "message": "Checking Fast Export capability in isolated Chromium."})
        if record["process"].stdout is not None:
            for line in record["process"].stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raw_worker_output.append(line.rstrip()[-4000:])
                    continue
                current = _read_status(status_path)
                if event.get("capability"):
                    record["capability"] = event["capability"]
                if event.get("browser"):
                    record["browser"] = event["browser"]
                _write_status(status_path, {**current, "status": "running", "stage": str(event.get("stage") or current.get("stage") or "preflight"), "progress": max(0, min(1, float(event.get("progress", current.get("progress", 0))))), "message": str(event.get("message") or current.get("message") or "Fast Export running."), "renderer": event, "capability": record.get("capability"), "browser": record.get("browser")})
        return_code = record["process"].wait()
        if record.get("cancelRequested"):
            _cleanup_unpromoted(job_dir)
            _write_status(status_path, {**_read_status(status_path), "status": "cancelled", "stage": "cancelled", "message": "Fast Export cancelled; no staged output was promoted.", "telemetry": record.get("telemetry")})
            return
        if return_code != 0:
            _cleanup_unpromoted(job_dir)
            detail = str(_read_status(status_path).get("renderer", {}).get("error") or "Worker exit code " + str(return_code))
            if raw_worker_output:
                detail += "\n" + "\n".join(raw_worker_output[-8:])
            _write_status(status_path, {**_read_status(status_path), "status": "failed", "stage": "failed", "message": "Fast Export worker failed.", "error": detail[-12000:], "workerOutput": raw_worker_output[-8:], "telemetry": record.get("telemetry")})
            return
        input_value = read_json(job_dir / "input.json", None)
        if not isinstance(input_value, dict):
            raise VideoRenderError("Fast Export input snapshot is missing.")
        snapshot = input_value["snapshot"]
        _validate_fast_snapshot_current(project_root, snapshot)
        staging = job_dir / "staging"
        video_only = Path(str(input_value["videoOnlyPath"]))
        audio_path = staging / "audio.m4a"
        muxed_path = staging / "album-video-fast.mp4"
        _write_status(status_path, {**_read_status(status_path), "stage": "audio-assembly", "progress": 0.74, "message": "Assembling frame-authoritative audio with FFmpeg."})
        plan = _assemble_audio(snapshot, project_root, audio_path, record)
        if record.get("cancelRequested"):
            raise VideoRenderError("Fast Export audio assembly was cancelled.")
        _write_status(status_path, {**_read_status(status_path), "stage": "muxing", "progress": 0.84, "message": "Muxing AAC while copying the H.264 stream."})
        mux = _mux_video(video_only, audio_path, muxed_path, record)
        _write_status(status_path, {**_read_status(status_path), "stage": "validating", "progress": 0.93, "message": "Validating Fast Export media, timestamps and continuity."})
        validation = _validate_fast_media(video_only, muxed_path, snapshot, plan, staging)
        metrics = {"telemetry": record.get("telemetry"), "videoTransfer": (record.get("renderer") or {}).get("evidence"), "mux": mux}
        _write_status(status_path, {**_read_status(status_path), "stage": "promoting", "progress": 0.98, "message": "Promoting the validated Fast Export atomically."})
        output, render_manifest = _promote_fast(project_root, job_id, muxed_path, snapshot, validation, plan, record.get("capability"), metrics)
        cleanup = ProjectArtifactLibrary(project_root).cleanup_validated_staging(staging_folder=staging, promoted_path=output, validation=validation)
        _write_status(status_path, {**_read_status(status_path), "status": "complete", "stage": "complete", "progress": 1, "message": "Fast Export promoted after validation.", "promotedPath": str(output), "renderManifestPath": str(render_manifest), "validation": validation, "stagingCleanup": cleanup, "telemetry": record.get("telemetry"), "capability": record.get("capability"), "browser": record.get("browser")})
    except Exception as exc:
        if record.get("cancelRequested"):
            _cleanup_unpromoted(job_dir)
            _write_status(status_path, {**_read_status(status_path), "status": "cancelled", "stage": "cancelled", "message": "Fast Export cancelled; no staged output was promoted.", "telemetry": record.get("telemetry")})
        else:
            _cleanup_unpromoted(job_dir)
            detail = f"{type(exc).__name__}: {exc}"
            _write_status(status_path, {**_read_status(status_path), "status": "failed", "stage": "failed", "message": str(exc), "error": detail, "workerOutput": raw_worker_output[-8:], "telemetry": record.get("telemetry")})
    finally:
        record["telemetryStop"].set()
        processes.pop(job_id, None)


def retry_fast_render(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    project_root = normalized_path(project_manifest).parent
    status = _read_status(_status_path(project_root, job_id))
    if status.get("status") not in {"failed", "cancelled", "interrupted"} or status.get("mode") != "fast":
        raise VideoRenderError("Only a failed, cancelled or interrupted Fast Export can be retried.")
    return start_fast_render(project_manifest, processes, synthetic=status.get("sourceKind") == "synthetic")
