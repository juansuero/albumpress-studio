from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .json_store import atomic_write_json, read_json
from .project_artifact_library import ArtifactLibraryError, ProjectArtifactLibrary
from .projects import ProjectError, normalized_path, utc_now
from .brand import brand_input_props, brand_snapshot_assets
from .video import build_video_state


JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")
RENDER_CONCURRENCY = 2
STATUS_IO_LOCK = threading.RLock()
SMOKE_TRACK_SECONDS = 4
SMOKE_TRACK_COUNT = 2
SMOKE_TOTAL_SECONDS = SMOKE_TRACK_SECONDS * SMOKE_TRACK_COUNT
RENDER_ROOT_RELATIVE = Path(".stem-comparison") / "work" / "video"
LEGACY_RENDER_ROOT_RELATIVE = Path(".stem-comparison") / "video-jobs"
NODE_SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "render-video-job.mjs"
_PROCESS_LOCK = threading.RLock()


class VideoRenderError(ProjectError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _job_root(project_root: Path) -> Path:
    return project_root / RENDER_ROOT_RELATIVE


def _legacy_job_root(project_root: Path) -> Path:
    return project_root / LEGACY_RENDER_ROOT_RELATIVE


def _job_dir(project_root: Path, job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise VideoRenderError("The video render job identifier is invalid.")
    modern = _job_root(project_root) / job_id
    legacy = _legacy_job_root(project_root) / job_id
    path = (legacy if legacy.exists() and not modern.exists() else modern).resolve(strict=False)
    try:
        path.relative_to((_legacy_job_root(project_root) if path.is_relative_to(_legacy_job_root(project_root)) else _job_root(project_root)).resolve(strict=False))
    except ValueError as exc:
        raise VideoRenderError("The video render job is outside the Project Folder.") from exc
    return path


def _status_path(project_root: Path, job_id: str) -> Path:
    return _job_dir(project_root, job_id) / "status.json"


def _write_status(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    value["updatedAt"] = utc_now()
    with STATUS_IO_LOCK:
        atomic_write_json(path, value)
    return value


def _read_status(path: Path) -> dict[str, Any]:
    with STATUS_IO_LOCK:
        value = read_json(path, None)
    if not isinstance(value, dict):
        raise VideoRenderError("The video render status is missing or unreadable.")
    return value


def _active_job(project_root: Path) -> str | None:
    roots = [root for root in (_job_root(project_root), _legacy_job_root(project_root)) if root.is_dir()]
    for root in roots:
        for status_path in root.glob("*/status.json"):
            try:
                status = _read_status(status_path)
            except VideoRenderError:
                continue
            if status.get("status") in {"queued", "running", "stopping"}:
                return str(status.get("jobId") or status_path.parent.name)
    return None


def _inside(root: Path, path: Path) -> Path:
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise VideoRenderError("A render snapshot asset is outside the active Project Folder.") from exc
    return candidate


def _write_tone(path: Path, frequency: float, seconds: float) -> None:
    sample_rate = 48_000
    amplitude = 0.24
    total = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(total):
            sample = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.extend(sample.to_bytes(2, "little", signed=True))
            frames.extend(sample.to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(frames))


def _code_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted((NODE_SCRIPT.parent.parent / "src" / "remotion").glob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _validate_snapshot_current(project_root: Path, snapshot: dict[str, Any]) -> None:
    fingerprints = snapshot.get("fingerprints") if isinstance(snapshot.get("fingerprints"), dict) else {}
    if fingerprints:
        for key, relative_path in (("projectManifest", "project.json"), ("configuration", "video/config.json")):
            expected = fingerprints.get(key)
            path = _inside(project_root, project_root / relative_path)
            if not isinstance(expected, str) or not path.is_file() or _sha256(path) != expected:
                raise VideoRenderError(f"Render snapshot is stale: {relative_path} changed before promotion.")
    if snapshot.get("codeFingerprint") != _code_fingerprint():
        raise VideoRenderError("Render snapshot is stale: the Remotion composition changed before promotion.")
    for key, asset in (snapshot.get("assets") or {}).items():
        path = _inside(project_root, project_root / str(asset.get("relativePath", "")))
        if not path.is_file() or _sha256(path) != asset.get("sha256"):
            raise VideoRenderError(f"Render snapshot is stale: asset {key} changed before promotion.")


def _snapshot(project_root: Path, job_id: str) -> dict[str, Any]:
    state = build_video_state(project_root / "project.json")
    if not state["ready"]:
        raise VideoRenderError("Video rendering is blocked: " + "; ".join(state["issues"]))
    job_dir = _job_dir(project_root, job_id)
    synthetic_dir = job_dir / "synthetic"
    synthetic_dir.mkdir(parents=True, exist_ok=True)
    audio_paths = [synthetic_dir / "audio-01.wav", synthetic_dir / "audio-02.wav"]
    _write_tone(audio_paths[0], 440, SMOKE_TRACK_SECONDS)
    _write_tone(audio_paths[1], 660, SMOKE_TRACK_SECONDS)
    config = state["config"]
    asset_paths: dict[str, Path] = {}
    render_asset_keys = ["artwork", "displayFont", "utilityFont"]
    if isinstance(config.get("assets"), dict) and "displayFontItalic" in config["assets"]:
        render_asset_keys.insert(2, "displayFontItalic")
    for key in render_asset_keys:
        record = config["assets"].get(key)
        if not isinstance(record, dict) or not record.get("path"):
            raise VideoRenderError(f"Render asset {key} is not registered.")
        asset_paths[key] = _inside(project_root, project_root / str(record["path"]))
        if not asset_paths[key].is_file():
            raise VideoRenderError(f"Render asset {key} is missing.")
    assets = {
        key: {"relativePath": asset_path.relative_to(project_root).as_posix(), "sha256": _sha256(asset_path), "bytes": asset_path.stat().st_size}
        for key, asset_path in asset_paths.items()
    }
    assets.update({
        f"audio-{index + 1}": {"relativePath": path.relative_to(project_root).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}
        for index, path in enumerate(audio_paths)
    })
    assets.update(brand_snapshot_assets(project_root, config.get("brand") or {}))
    tracks = [
        {"trackId": "synthetic-01", "sequence": 1, "title": "Synthetic Boundary One", "durationSeconds": SMOKE_TRACK_SECONDS, "audioKey": "audio-1"},
        {"trackId": "synthetic-02", "sequence": 2, "title": "Synthetic Boundary Two", "durationSeconds": SMOKE_TRACK_SECONDS, "audioKey": "audio-2"},
    ]
    return {
        "snapshotVersion": 1,
        "kind": "synthetic-two-track-boundary-smoke",
        "projectFolder": str(project_root),
        "projectManifest": "project.json",
        "configuration": "video/config.json",
        "codeFingerprint": _code_fingerprint(),
        "fingerprints": {
            "projectManifest": _sha256(project_root / "project.json"),
            "configuration": _sha256(project_root / "video" / "config.json"),
        },
        "assets": assets,
        "tracks": tracks,
        "expected": {"width": 1920, "height": 1080, "fps": 30, "durationSeconds": SMOKE_TOTAL_SECONDS, "videoCodec": "h264", "audioCodec": "aac", "audioSampleRate": 48000, "audioChannels": 2},
        "settings": {"codec": "h264", "audioCodec": "aac", "crf": 23, "pixelFormat": "yuv420p", "colorSpace": "bt709", "concurrency": RENDER_CONCURRENCY, "audioTransform": "AAC-LC encoding only"},
        "props": {
            "artist": str(config.get("artist", "Synthetic Smoke")),
            "album": str(config.get("album", "Ticket 12 Boundary")),
            "displayFontFamily": config["typography"]["displayFontFamily"],
            "utilityFontFamily": config["typography"]["utilityFontFamily"],
            "colors": config["colors"],
            "cinematicFinish": config["cinematicFinish"],
            "reducedMotion": False,
            "artworkKey": "artwork",
            "displayFontKey": "displayFont",
            "utilityFontKey": "utilityFont",
            **({"displayFontItalicKey": "displayFontItalic"} if "displayFontItalic" in assets else {}),
            "tracks": tracks,
            "brand": brand_input_props(config.get("brand") or {}),
        },
    }


def _real_snapshot(project_root: Path) -> dict[str, Any]:
    state = build_video_state(project_root / "project.json")
    if not state["ready"]:
        raise VideoRenderError("Real album rendering is blocked: " + "; ".join(state["issues"]))
    config = state["config"]
    asset_paths: dict[str, Path] = {}
    render_asset_keys = ["artwork", "displayFont", "utilityFont"]
    if isinstance(config.get("assets"), dict) and "displayFontItalic" in config["assets"]:
        render_asset_keys.insert(2, "displayFontItalic")
    for key in render_asset_keys:
        record = config["assets"].get(key)
        if not isinstance(record, dict) or not record.get("path"):
            raise VideoRenderError(f"Render asset {key} is not registered.")
        asset_paths[key] = _inside(project_root, project_root / str(record["path"]))
        if not asset_paths[key].is_file():
            raise VideoRenderError(f"Render asset {key} is missing.")
    assets = {
        key: {"relativePath": asset_path.relative_to(project_root).as_posix(), "sha256": _sha256(asset_path), "bytes": asset_path.stat().st_size}
        for key, asset_path in asset_paths.items()
    }
    assets.update(brand_snapshot_assets(project_root, config.get("brand") or {}))
    tracks = []
    selection_snapshot = []
    for item in state["composition"]["timeline"]:
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
    return {
        "snapshotVersion": 1,
        "kind": "real-album-landscape",
        "projectFolder": str(project_root),
        "projectManifest": "project.json",
        "configuration": "video/config.json",
        "codeFingerprint": _code_fingerprint(),
        "fingerprints": {"projectManifest": _sha256(project_root / "project.json"), "configuration": _sha256(project_root / "video" / "config.json")},
        "assets": assets,
        "tracks": tracks,
        "selectionSnapshot": selection_snapshot,
        "expected": {"width": 1920, "height": 1080, "fps": 30, "durationSeconds": float(state["composition"]["durationSeconds"]), "videoCodec": "h264", "audioCodec": "aac", "audioSampleRate": 48000, "audioChannels": 2},
        "settings": {"codec": "h264", "audioCodec": "aac", "crf": 23, "pixelFormat": "yuv420p", "colorSpace": "bt709", "concurrency": RENDER_CONCURRENCY, "audioTransform": "AAC-LC encoding only"},
        "props": {
            "artist": str(config.get("artist", "")),
            "album": str(config.get("album", "")),
            "displayFontFamily": config["typography"]["displayFontFamily"],
            "utilityFontFamily": config["typography"]["utilityFontFamily"],
            "colors": config["colors"],
            "cinematicFinish": config["cinematicFinish"],
            "reducedMotion": bool(config.get("reducedMotion", False)),
            "artworkKey": "artwork",
            "displayFontKey": "displayFont",
            "utilityFontKey": "utilityFont",
            **({"displayFontItalicKey": "displayFontItalic"} if "displayFontItalic" in assets else {}),
            "tracks": tracks,
            "brand": brand_input_props(config.get("brand") or {}),
        },
    }


def _node_path() -> str:
    candidate = shutil.which("node") or shutil.which("node.exe")
    if candidate:
        return candidate
    fallback = Path("C:/Program Files/nodejs/node.exe")
    if fallback.is_file():
        return str(fallback)
    raise VideoRenderError("Node.js is required for Remotion rendering.")


def _terminate_process_tree(process: Any) -> None:
    pid = getattr(process, "pid", None)
    if pid and os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        return
    if process.poll() is None:
        process.terminate()


def start_synthetic_render(project_manifest: str | Path, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    project_root = manifest_path.parent
    with _PROCESS_LOCK:
        active = _active_job(project_root)
        if active:
            raise VideoRenderError(f"Video render job {active} is already active.")
        job_id = uuid.uuid4().hex[:12]
        job_dir = _job_dir(project_root, job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        snapshot = _snapshot(project_root, job_id)
        input_path = job_dir / "input.json"
        output_path = job_dir / "staging" / "album-landscape-smoke.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(input_path, {"jobId": job_id, "projectFolder": str(project_root), "snapshot": snapshot, "outputPath": str(output_path), "concurrency": RENDER_CONCURRENCY, "ticketId": "ticket-12", "outputFilename": "album-landscape-smoke.mp4"})
        status_path = job_dir / "status.json"
        _write_status(status_path, {
            "jobId": job_id,
            "kind": "synthetic",
            "sourceKind": "synthetic",
            "mode": "reference",
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "Synthetic Ticket 12 render queued.",
            "concurrency": RENDER_CONCURRENCY,
            "inputPath": str(input_path),
            "stagingPath": str(output_path),
            "promotedPath": None,
            "snapshot": snapshot,
        })
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                [_node_path(), str(NODE_SCRIPT), str(input_path)],
                cwd=str(NODE_SCRIPT.parent.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            _write_status(status_path, {"jobId": job_id, "kind": "synthetic", "status": "failed", "stage": "failed", "progress": 0, "message": str(exc), "error": str(exc)})
            raise VideoRenderError("The Remotion renderer process could not start.") from exc
        processes[job_id] = {"process": process, "statusPath": status_path, "cancelRequested": False}
        threading.Thread(target=_monitor, args=(project_root, job_id, processes), daemon=True).start()
        return _read_status(status_path)


def start_real_render(project_manifest: str | Path, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    project_root = manifest_path.parent
    from .video_proof import VideoProofError, require_approved_proof

    with _PROCESS_LOCK:
        active = _active_job(project_root)
        if active:
            raise VideoRenderError(f"Video render job {active} is already active.")
        try:
            require_approved_proof(project_root)
        except VideoProofError as exc:
            raise VideoRenderError(str(exc)) from exc
        job_id = uuid.uuid4().hex[:12]
        job_dir = _job_dir(project_root, job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        snapshot = _real_snapshot(project_root)
        input_path = job_dir / "input.json"
        output_path = job_dir / "staging" / "album-landscape.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(input_path, {"jobId": job_id, "projectFolder": str(project_root), "snapshot": snapshot, "outputPath": str(output_path), "concurrency": RENDER_CONCURRENCY, "ticketId": "ticket-14", "outputFilename": "album-landscape.mp4"})
        status_path = job_dir / "status.json"
        _write_status(status_path, {
            "jobId": job_id,
            "kind": "real-album",
            "sourceKind": "real",
            "mode": "reference",
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "The authorized Little Songs Album Video render is queued.",
            "concurrency": RENDER_CONCURRENCY,
            "inputPath": str(input_path),
            "stagingPath": str(output_path),
            "promotedPath": None,
            "snapshot": snapshot,
        })
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                [_node_path(), str(NODE_SCRIPT), str(input_path)],
                cwd=str(NODE_SCRIPT.parent.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            _write_status(status_path, {"jobId": job_id, "kind": "real-album", "status": "failed", "stage": "failed", "progress": 0, "message": str(exc), "error": str(exc)})
            raise VideoRenderError("The authorized Album Video renderer process could not start.") from exc
        processes[job_id] = {"process": process, "statusPath": status_path, "cancelRequested": False}
        threading.Thread(target=_monitor, args=(project_root, job_id, processes), daemon=True).start()
        return _read_status(status_path)


def _validate_mp4(path: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise VideoRenderError("The staged MP4 is missing or empty.")
    ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if not ffprobe:
        raise VideoRenderError("FFprobe is required to validate the staged MP4.")
    completed = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,color_space,sample_rate,channels", "-of", "json", str(path)], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise VideoRenderError((completed.stderr or "FFprobe could not read the staged MP4.").strip())
    try:
        metadata = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoRenderError("FFprobe returned invalid JSON for the staged MP4.") from exc
    streams = metadata.get("streams") if isinstance(metadata.get("streams"), list) else []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    expected = snapshot["expected"]
    checks = {
        "videoStream": isinstance(video_stream, dict),
        "audioStream": isinstance(audio_stream, dict),
        "dimensions": isinstance(video_stream, dict) and video_stream.get("width") == expected["width"] and video_stream.get("height") == expected["height"],
        "fps": isinstance(video_stream, dict) and str(video_stream.get("r_frame_rate")) == f"{expected['fps']}/1",
        "videoCodec": isinstance(video_stream, dict) and video_stream.get("codec_name") == expected["videoCodec"],
        "pixelFormat": isinstance(video_stream, dict) and video_stream.get("pix_fmt") == "yuv420p",
        "colorSpace": isinstance(video_stream, dict) and video_stream.get("color_space") in {"bt709", "unknown"},
        "audioCodec": isinstance(audio_stream, dict) and audio_stream.get("codec_name") == expected["audioCodec"],
        "sampleRate": isinstance(audio_stream, dict) and int(audio_stream.get("sample_rate", 0)) == expected["audioSampleRate"],
        "channels": isinstance(audio_stream, dict) and int(audio_stream.get("channels", 0)) == expected["audioChannels"],
        "duration": abs(float(metadata.get("format", {}).get("duration", 0)) - expected["durationSeconds"]) <= 0.2,
    }
    if not all(checks.values()):
        raise VideoRenderError("Staged MP4 failed technical validation: " + ", ".join(key for key, value in checks.items() if not value))
    return {"checks": checks, "ffprobe": metadata, "sha256": _sha256(path), "bytes": path.stat().st_size}


def _promote(project_root: Path, job_id: str, staging_path: Path, snapshot: dict[str, Any], validation: dict[str, Any], *, ticket_id: str = "ticket-12", output_filename: str = "album-landscape-smoke.mp4") -> tuple[Path, Path]:
    project = read_json(project_root / "project.json", {})
    artifact_library = project.get("artifactLibrary") if isinstance(project, dict) and isinstance(project.get("artifactLibrary"), dict) else {}
    if int(artifact_library.get("layoutVersion", 0) or 0) >= 1:
        try:
            return ProjectArtifactLibrary(project_root).promote_validated_render(job_id=job_id, snapshot=snapshot, validation=validation, staging_path=staging_path, ticket_id=ticket_id, output_filename=output_filename)
        except ArtifactLibraryError as exc:
            raise VideoRenderError(str(exc)) from exc
    base = project_root / "video" / "renders" / ticket_id
    version = 1
    while (base / f"v{version}" / output_filename).exists():
        version += 1
    destination = base / f"v{version}"
    destination.mkdir(parents=True, exist_ok=False)
    output = destination / output_filename
    shutil.copy2(staging_path, output)
    manifest_path = destination / "render-manifest.json"
    atomic_write_json(manifest_path, {"schemaVersion": 1, "jobId": job_id, "kind": snapshot["kind"], "outputPath": output.relative_to(project_root).as_posix(), "snapshot": snapshot, "validation": validation, "promotedAt": utc_now()})
    return output, manifest_path


def _monitor(project_root: Path, job_id: str, processes: dict[str, dict[str, Any]]) -> None:
    record = processes[job_id]
    process = record["process"]
    status_path = record["statusPath"]
    try:
        _write_status(status_path, {**_read_status(status_path), "status": "running", "stage": "bundling", "progress": 0, "message": "Bundling shared Remotion composition once."})
        if process.stdout is not None:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                current = _read_status(status_path)
                stage = str(event.get("stage") or current.get("stage") or "rendering")
                progress = float(event.get("progress", current.get("progress", 0)))
                _write_status(status_path, {**current, "status": "running", "stage": stage, "progress": max(0, min(1, progress)), "message": str(event.get("message") or stage), "renderer": event})
        return_code = process.wait()
        current = _read_status(status_path)
        if record.get("cancelRequested"):
            _write_status(status_path, {**current, "status": "cancelled", "stage": "cancelled", "message": "Render cancelled; no staged output was promoted."})
            return
        if return_code != 0:
            _write_status(status_path, {**current, "status": "failed", "stage": "failed", "message": "Remotion renderer failed.", "error": f"Exit code {return_code}"})
            return
        _write_status(status_path, {**current, "status": "running", "stage": "validating", "progress": 1, "message": "Validating the staged MP4 with FFprobe."})
        input_value = read_json(_job_dir(project_root, job_id) / "input.json", None)
        if not isinstance(input_value, dict):
            raise VideoRenderError("Render input snapshot is missing.")
        staging_path = Path(str(input_value["outputPath"]))
        _validate_snapshot_current(project_root, input_value["snapshot"])
        validation = _validate_mp4(staging_path, input_value["snapshot"])
        ticket_id = str(input_value.get("ticketId") or "ticket-12")
        output_filename = str(input_value.get("outputFilename") or "album-landscape-smoke.mp4")
        _write_status(status_path, {**_read_status(status_path), "stage": "promoting", "message": "Promoting the validated Album Video render."})
        output, render_manifest = _promote(project_root, job_id, staging_path, input_value["snapshot"], validation, ticket_id=ticket_id, output_filename=output_filename)
        cleanup = ProjectArtifactLibrary(project_root).cleanup_validated_staging(staging_folder=staging_path.parent, promoted_path=output, validation=validation)
        _write_status(status_path, {**_read_status(status_path), "status": "complete", "stage": "complete", "progress": 1, "message": "Album Video promoted after validation.", "promotedPath": str(output), "renderManifestPath": str(render_manifest), "validation": validation, "stagingCleanup": cleanup})
    except Exception as exc:
        current = _read_status(status_path) if status_path.exists() else {"jobId": job_id}
        _write_status(status_path, {**current, "status": "failed", "stage": "failed", "message": str(exc), "error": str(exc)})
    finally:
        processes.pop(job_id, None)


def read_video_render_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    project_root = normalized_path(project_manifest).parent
    status_path = _status_path(project_root, job_id)
    if not status_path.is_file():
        raise VideoRenderError("Video render job not found.")
    status = _read_status(status_path)
    if status.get("status") in {"queued", "running", "stopping"} and job_id not in processes:
        return _write_status(status_path, {**status, "status": "interrupted", "stage": "interrupted", "message": "Render was interrupted by a backend restart; retry starts a new render."})
    return status


def stop_video_render_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    project_root = normalized_path(project_manifest).parent
    status = read_video_render_job(project_manifest, job_id, processes)
    record = processes.get(job_id)
    if record is None or status.get("status") not in {"queued", "running", "stopping"}:
        return status
    record["cancelRequested"] = True
    cancel_callback = record.get("cancel")
    if callable(cancel_callback):
        cancel_callback()
    child = record.get("childProcess")
    if child is not None:
        _terminate_process_tree(child)
    _terminate_process_tree(record["process"])
    return _write_status(_status_path(project_root, job_id), {**status, "status": "stopping", "stage": "cancelling", "message": "Cancellation requested; previous valid renders remain untouched."})


def retry_synthetic_render(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status = read_video_render_job(project_manifest, job_id, processes)
    if status.get("status") not in {"failed", "cancelled", "interrupted"}:
        raise VideoRenderError("Only a failed, cancelled, or interrupted render can be retried.")
    return start_synthetic_render(project_manifest, processes)


def retry_video_render(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status = read_video_render_job(project_manifest, job_id, processes)
    if status.get("mode") == "fast":
        from .video_fast_export import retry_fast_render

        return retry_fast_render(project_manifest, job_id, processes)
    if status.get("kind") == "real-album":
        return start_real_render(project_manifest, processes)
    return retry_synthetic_render(project_manifest, job_id, processes)
