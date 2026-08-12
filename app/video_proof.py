from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from .brand import brand_snapshot_assets
from .json_store import atomic_write_json, read_json
from .projects import ProjectError, normalized_path, utc_now
from .video import build_video_state, _png_dimensions
from .video_package import _human_track_title
from .video_render import (
    _active_job,
    _code_fingerprint,
    _inside,
    _node_path,
    _real_snapshot,
    _sha256,
    _write_status,
)


PROOF_SCHEMA_VERSION = 2
PROOF_RECIPE_VERSION = "release-proof-v2"
PROOF_JOB_ID_RE = uuid.UUID
PROOF_JOB_ROOT = Path(".stem-comparison") / "work" / "video-proof"
PROOF_NODE_SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "render-proof-pack.mjs"
PROOF_ROOT = Path("video") / "proofs"
PROOF_POINTER = PROOF_ROOT / "current.json"
PROOF_FPS = 30
PROOF_WIDTH = 1920
PROOF_HEIGHT = 1080
PROOF_VIDEO_SECONDS = 10
PROOF_OPENING_SECONDS = 8
PROOF_CLOSING_SECONDS = 8
PROOF_AUDIO_SILENCE_DB = -90.0
# Frame 0 is covered by the production fade-in; use the first stable artwork frame.
PROOF_THUMBNAIL_FRAME = PROOF_FPS


class VideoProofError(ProjectError):
    pass


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _proof_job_dir(root: Path, job_id: str) -> Path:
    try:
        uuid.UUID(hex=job_id)
    except (ValueError, AttributeError) as exc:
        raise VideoProofError("The proof job identifier is invalid.") from exc
    path = (root / PROOF_JOB_ROOT / job_id).resolve(strict=False)
    try:
        path.relative_to((root / PROOF_JOB_ROOT).resolve(strict=False))
    except ValueError as exc:
        raise VideoProofError("The proof job is outside the Project Folder.") from exc
    return path


def _proof_status_path(root: Path, job_id: str) -> Path:
    return _proof_job_dir(root, job_id) / "status.json"


def _read_proof_status(root: Path, job_id: str) -> dict[str, Any]:
    value = read_json(_proof_status_path(root, job_id), None)
    if not isinstance(value, dict):
        raise VideoProofError("The Proof Pack job status is missing or unreadable.")
    return value


def _proof_active_job(root: Path) -> str | None:
    active = _active_job(root)
    if active:
        return active
    job_root = root / PROOF_JOB_ROOT
    if not job_root.is_dir():
        return None
    for status_path in job_root.glob("*/status.json"):
        status = read_json(status_path, {})
        if isinstance(status, dict) and status.get("status") in {"queued", "running", "stopping"}:
            return str(status.get("jobId") or status_path.parent.name)
    return None


def _font_is_readable(path: Path) -> bool:
    try:
        return path.read_bytes()[:4] in {b"wOF2", b"wOFF", b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}
    except OSError:
        return False


def _asset_record(root: Path, path: Path) -> dict[str, Any]:
    path = _inside(root, path)
    if not path.is_file():
        raise VideoProofError(f"Proof asset is missing: {path}")
    return {"relativePath": path.relative_to(root).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}


def _real_proof_snapshot(project_root: Path) -> dict[str, Any]:
    state = build_video_state(project_root / "project.json")
    if not state["ready"]:
        raise VideoProofError("Proof generation is blocked: " + "; ".join(state["issues"]))
    config = state["config"]
    for key in ("displayFont", "utilityFont"):
        record = config.get("assets", {}).get(key)
        path = _inside(project_root, project_root / str(record.get("path", ""))) if isinstance(record, dict) else None
        if not path or not _font_is_readable(path):
            raise VideoProofError(f"The configured {key} is missing or unreadable. relink or replace the font before generating a Proof Pack.")
    snapshot = _real_snapshot(project_root)
    state_tracks = {str(item.get("trackId")): item for item in state["composition"].get("timeline", [])}
    for track in snapshot.get("tracks", []):
        source = state_tracks.get(str(track.get("trackId")), {})
        for field in ("originalDurationSeconds", "effectiveDurationSeconds", "trailingSilenceSeconds", "retainedTailSeconds", "proposedRemovalSeconds", "silenceStatus", "silenceConfidence", "preparationOverride"):
            if field in source:
                track[field] = source[field]
    texture = config.get("assets", {}).get("texture") if isinstance(config.get("assets"), dict) else None
    if isinstance(texture, dict) and texture.get("path"):
        texture_path = _inside(project_root, project_root / str(texture["path"]))
        snapshot["assets"]["texture"] = _asset_record(project_root, texture_path)
        snapshot["props"]["textureKey"] = "texture"
    preparation = config.get("preparation") if isinstance(config.get("preparation"), dict) else {}
    settings = preparation.get("settings") if isinstance(preparation.get("settings"), dict) else {}
    snapshot["props"].update({
        "includeAudio": True,
        "fadeInSeconds": float(settings.get("audioFadeInSeconds", 1.0)),
        "fadeOutSeconds": float(settings.get("audioFadeOutSeconds", 2.0)),
    })
    snapshot["settings"] = {
        **snapshot.get("settings", {}),
        "proofRenderer": "Remotion renderMedia video-only plus FFmpeg audio mux with inclusive frameRange",
        "proofAudio": "current Final Instrumentals trimmed and concatenated by frame range, then AAC muxed",
    }
    snapshot["proofVersion"] = PROOF_SCHEMA_VERSION
    snapshot["mode"] = "reference-proof"
    return snapshot


def _synthetic_proof_snapshot(project_root: Path, job_id: str) -> dict[str, Any]:
    from .video_render import _snapshot

    snapshot = _snapshot(project_root, job_id)
    cursor = 0
    for track in snapshot["tracks"]:
        track["startFrame"] = cursor
        track["durationInFrames"] = round(float(track["durationSeconds"]) * PROOF_FPS)
        cursor += track["durationInFrames"]
    snapshot["expected"]["frameCount"] = cursor
    snapshot["expected"]["durationSeconds"] = cursor / PROOF_FPS
    snapshot["props"]["includeAudio"] = True
    snapshot["proofVersion"] = PROOF_SCHEMA_VERSION
    return snapshot


def _fingerprint_payload(snapshot: dict[str, Any], selection: dict[str, Any] | None = None) -> dict[str, Any]:
    asset_payload = {
        key: {field: asset.get(field) for field in ("relativePath", "sha256", "bytes")}
        for key, asset in sorted((snapshot.get("assets") or {}).items())
        if isinstance(asset, dict)
    }
    track_payload = [
        {field: track.get(field) for field in ("trackId", "sequence", "title", "durationSeconds", "startFrame", "durationInFrames", "outputId", "fileFingerprint", "finalPath", "audioKey")}
        for track in sorted(snapshot.get("tracks", []), key=lambda item: int(item.get("sequence", 0)))
    ]
    props = snapshot.get("props") if isinstance(snapshot.get("props"), dict) else {}
    renderer_settings = {
        "compositionId": "AlbumLandscape",
        "width": snapshot.get("expected", {}).get("width", PROOF_WIDTH),
        "height": snapshot.get("expected", {}).get("height", PROOF_HEIGHT),
        "fps": snapshot.get("expected", {}).get("fps", PROOF_FPS),
        "videoCodec": "h264",
        "audioCodec": "aac",
        "pixelFormat": "yuv420p",
        "colorSpace": "bt709",
        "includeAudio": props.get("includeAudio", True),
        "cinematicFinish": props.get("cinematicFinish"),
        "fadeInSeconds": props.get("fadeInSeconds"),
        "fadeOutSeconds": props.get("fadeOutSeconds"),
        "renderMediaConcurrency": 2,
        "renderMediaCrf": 23,
        "frameRangeInclusive": True,
        "thumbnailScale": 2 / 3,
        "assetTransport": "local-http-range-server",
        "audioAssembly": "FFmpeg PCM source trim/concat plus AAC-LC mux",
    }
    return {
        "schemaVersion": PROOF_SCHEMA_VERSION,
        "recipeVersion": PROOF_RECIPE_VERSION,
        "snapshot": snapshot,
        "selection": selection if selection is not None else _selection_plan(snapshot),
        "renderer": {
            "script": {"relativePath": "frontend/scripts/render-proof-pack.mjs", "sha256": _sha256(PROOF_NODE_SCRIPT), "bytes": PROOF_NODE_SCRIPT.stat().st_size},
            "settings": renderer_settings,
        },
        "snapshotSummary": {
            "projectManifestSha256": snapshot.get("fingerprints", {}).get("projectManifest"),
            "configurationSha256": snapshot.get("fingerprints", {}).get("configuration"),
            "codeFingerprint": snapshot.get("codeFingerprint"),
            "assets": asset_payload,
            "tracks": track_payload,
            "expected": snapshot.get("expected"),
        },
        "visualProps": {
            key: props.get(key)
            for key in ("artist", "album", "displayFontFamily", "utilityFontFamily", "colors", "cinematicFinish", "reducedMotion", "brand", "fadeInSeconds", "fadeOutSeconds")
        },
    }


def proof_input_fingerprint(snapshot: dict[str, Any], selection: dict[str, Any] | None = None) -> str:
    return _digest(_fingerprint_payload(snapshot, selection))


def _timeline_total(snapshot: dict[str, Any]) -> int:
    expected = snapshot.get("expected") if isinstance(snapshot.get("expected"), dict) else {}
    value = expected.get("frameCount")
    if value is not None:
        return int(value)
    return max((int(track.get("startFrame", 0)) + int(track.get("durationInFrames", 0)) for track in snapshot.get("tracks", [])), default=0)


def _clip(start: int, end: int, total: int) -> dict[str, int]:
    start = max(0, min(start, max(0, total - 1)))
    end = max(start, min(end, max(0, total - 1)))
    return {"startFrame": start, "endFrame": end, "frameCount": end - start + 1, "durationSeconds": round((end - start + 1) / PROOF_FPS, 6)}


def _selection_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    tracks = sorted(snapshot.get("tracks", []), key=lambda item: int(item.get("sequence", 0)))
    total = _timeline_total(snapshot)
    if not tracks or total <= 0:
        raise VideoProofError("The current effective timeline has no renderable Tracks.")
    longest = max(tracks, key=lambda item: (len(_human_track_title(str(item.get("title", "")))), -int(item.get("sequence", 0))))
    transition_tracks = tracks[:-1] or tracks
    risk = max(transition_tracks, key=lambda item: (float(item.get("proposedRemovalSeconds", 0) or 0), bool(item.get("preparationOverride")), -int(item.get("sequence", 0))))
    standard = next((item for item in transition_tracks if item.get("trackId") != risk.get("trackId")), transition_tracks[0])

    def boundary_after(track: dict[str, Any]) -> int:
        return int(track.get("startFrame", 0)) + int(track.get("durationInFrames", 0))

    def transition_case(case_id: str, track: dict[str, Any], reason: str) -> dict[str, Any]:
        boundary = boundary_after(track)
        return {
            "caseId": case_id,
            "kind": "transition",
            "trackIds": [track.get("trackId"), next((item.get("trackId") for item in tracks if int(item.get("sequence", 0)) == int(track.get("sequence", 0)) + 1), None)],
            "trackSequences": [int(track.get("sequence", 0)), int(track.get("sequence", 0)) + 1],
            "reason": reason,
            "boundaryFrame": boundary,
            "boundarySeconds": round(boundary / PROOF_FPS, 6),
            **_clip(boundary - 5 * PROOF_FPS, boundary + 5 * PROOF_FPS - 1, total),
        }

    longest_start = int(longest.get("startFrame", 0))
    plan = {
        "opening": {"caseId": "opening", "kind": "opening", "trackIds": [tracks[0].get("trackId")], "trackSequences": [int(tracks[0].get("sequence", 1))], "reason": "Opening ident and the first real Track.", **_clip(0, PROOF_OPENING_SECONDS * PROOF_FPS - 1, total)},
        "transition-standard": transition_case("transition-standard", standard, "Representative interior boundary selected deterministically away from the highest-risk trim."),
        "transition-risk": transition_case("transition-risk", risk, f"Highest approved proposed removal among transition-capable Tracks: {float(risk.get('proposedRemovalSeconds', 0) or 0):.3f}s; override={risk.get('preparationOverride') or 'automatic'}; boundary after Track {risk.get('sequence')}.") ,
        "long-title": {"caseId": "long-title", "kind": "still", "trackIds": [longest.get("trackId")], "trackSequences": [int(longest.get("sequence", 1))], "reason": f"Longest humanized Track title ({len(_human_track_title(str(longest.get('title', ''))))} characters).", "frame": min(longest_start + PROOF_FPS, max(longest_start, total - 1)), "title": _human_track_title(str(longest.get("title", "")))},
        "closing": {"caseId": "closing", "kind": "closing", "trackIds": [tracks[-1].get("trackId")], "trackSequences": [int(tracks[-1].get("sequence", 1))], "reason": "Terminal lockup, final fade and black frame.", **_clip(total - PROOF_CLOSING_SECONDS * PROOF_FPS, total - 1, total)},
        "thumbnail": {"caseId": "thumbnail", "kind": "thumbnail", "trackIds": [tracks[0].get("trackId")], "trackSequences": [int(tracks[0].get("sequence", 1))], "reason": f"Configured artwork/font/brand thumbnail state at stable frame {min(PROOF_THUMBNAIL_FRAME, max(0, total - 1))}.", "frame": min(PROOF_THUMBNAIL_FRAME, max(0, total - 1)), "width": 1280, "height": 720},
    }
    return plan


def _proof_paths(root: Path, proof_id: str) -> dict[str, str]:
    folder = PROOF_ROOT / proof_id
    return {
        "opening": (folder / "opening.mp4").as_posix(),
        "transition-standard": (folder / "transition-standard.mp4").as_posix(),
        "transition-risk": (folder / "transition-risk.mp4").as_posix(),
        "long-title": (folder / "long-title.png").as_posix(),
        "closing": (folder / "closing.mp4").as_posix(),
        "thumbnail": (folder / "thumbnail.png").as_posix(),
    }


def build_proof_preview(project_manifest: str | Path, *, synthetic: bool = False) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    snapshot = _real_proof_snapshot(root) if not synthetic else _synthetic_proof_snapshot(root, uuid.uuid4().hex[:12])
    selection = _selection_plan(snapshot)
    fingerprint = proof_input_fingerprint(snapshot, selection)
    return {"inputFingerprint": fingerprint, "selection": selection, "snapshot": snapshot, "fingerprintPayload": _fingerprint_payload(snapshot, selection), "synthetic": synthetic}


def _ffprobe(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if not executable:
        raise VideoProofError("FFprobe is required to validate each Proof Pack artifact.")
    command = [executable, "-v", "error", "-count_frames", "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,color_space,color_primaries,sample_rate,channels,nb_read_frames,duration,start_time,time_base", "-of", "json", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoProofError((completed.stderr or "FFprobe could not read the proof artifact.").strip())
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoProofError("FFprobe returned invalid JSON for a proof artifact.") from exc
    if not isinstance(value, dict):
        raise VideoProofError("FFprobe returned an invalid proof payload.")
    return value


def _packet_timestamps(path: Path, stream: str) -> dict[str, Any]:
    executable = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    completed = subprocess.run([executable, "-v", "error", "-select_streams", stream, "-show_entries", "packet=pts_time,dts_time", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoProofError("FFprobe could not inspect Proof Pack timestamps.")
    values = [float(item["pts_time"]) for item in json.loads(completed.stdout).get("packets", []) if item.get("pts_time") not in {None, "N/A"}]
    return {"count": len(values), "monotonic": all(current >= previous for previous, current in zip(values, values[1:])), "first": values[0] if values else None, "last": values[-1] if values else None}


def _frame_timestamps(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    completed = subprocess.run([executable, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoProofError("FFprobe could not inspect Proof Pack video frames.")
    values = [float(item["best_effort_timestamp_time"]) for item in json.loads(completed.stdout).get("frames", []) if item.get("best_effort_timestamp_time") not in {None, "N/A"}]
    return {"count": len(values), "monotonic": all(current >= previous for previous, current in zip(values, values[1:])), "first": values[0] if values else None, "last": values[-1] if values else None}


def _audio_activity(path: Path) -> dict[str, float]:
    executable = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not executable:
        raise VideoProofError("FFmpeg is required to validate Proof Pack audio activity.")
    completed = subprocess.run([executable, "-hide_banner", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "NUL"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoProofError((completed.stderr or "FFmpeg could not inspect Proof Pack audio activity.").strip())
    import re

    max_match = re.search(r"max_volume:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s*dB", completed.stderr)
    mean_match = re.search(r"mean_volume:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s*dB", completed.stderr)
    if not max_match or not mean_match:
        raise VideoProofError("FFmpeg did not return measurable Proof Pack audio activity.")

    def parse(value: str) -> float:
        return -120.0 if value == "-inf" else float(value)

    return {"maxVolumeDb": parse(max_match.group(1)), "meanVolumeDb": parse(mean_match.group(1))}


def _extract_frame(source: Path, destination: Path, *, timestamp: float = 0.0) -> None:
    executable = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not executable:
        raise VideoProofError("FFmpeg is required for representative Proof Pack frames.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, timestamp):.6f}", "-i", str(source), "-frames:v", "1", "-y", str(destination)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise VideoProofError((completed.stderr or "Could not extract a representative proof frame.").strip())


def _validate_video(path: Path, spec: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    metadata = _ffprobe(path)
    streams = metadata.get("streams") if isinstance(metadata.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    expected_frames = int(spec["frameCount"])
    frame_count = int(video.get("nb_read_frames", 0) or 0)
    container_duration = float(metadata.get("format", {}).get("duration", 0) or 0)
    duration = float(video.get("duration", 0) or 0) or container_duration
    video_timestamps = _packet_timestamps(path, "v:0")
    video_frames = _frame_timestamps(path)
    audio_timestamps = _packet_timestamps(path, "a:0")
    audio_activity = _audio_activity(path)
    checks = {
        "video": bool(video),
        "audio": bool(audio),
        "audioActivity": audio_activity["maxVolumeDb"] > PROOF_AUDIO_SILENCE_DB,
        "dimensions": video.get("width") == PROOF_WIDTH and video.get("height") == PROOF_HEIGHT,
        "h264": video.get("codec_name") == "h264",
        "cfr30": str(video.get("r_frame_rate")) == "30/1" and str(video.get("avg_frame_rate")) == "30/1",
        "pixelFormat": video.get("pix_fmt") == "yuv420p",
        "color": video.get("color_space") == "bt709" or video.get("color_primaries") == "bt709",
        "aacStereo48": audio.get("codec_name") == "aac" and int(audio.get("sample_rate", 0) or 0) == 48000 and int(audio.get("channels", 0) or 0) == 2,
        "frames": frame_count == expected_frames,
        "duration": abs(duration - float(spec["durationSeconds"])) <= 1 / PROOF_FPS,
        "videoTimestampsMonotonic": video_frames["monotonic"],
        "audioTimestampsMonotonic": audio_timestamps["monotonic"],
    }
    if not all(checks.values()):
        raise VideoProofError(f"Proof video failed validation: {', '.join(key for key, value in checks.items() if not value)}")
    return {"checks": checks, "ffprobe": metadata, "timestamps": {"videoPackets": video_timestamps, "videoFrames": video_frames, "audio": audio_timestamps}, "audioActivity": audio_activity, "sha256": _sha256(path), "bytes": path.stat().st_size, "frameCount": frame_count, "durationSeconds": duration, "containerDurationSeconds": container_duration}


def _validate_still(path: Path, expected_width: int, expected_height: int) -> dict[str, Any]:
    dimensions = _png_dimensions(path)
    checks = {"png": path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "dimensions": dimensions == (expected_width, expected_height)}
    if not all(checks.values()):
        raise VideoProofError("Proof still failed validation: " + ", ".join(key for key, value in checks.items() if not value))
    return {"checks": checks, "dimensions": {"width": expected_width, "height": expected_height}, "sha256": _sha256(path), "bytes": path.stat().st_size}


def _validate_and_manifest(staging_proof: Path, snapshot: dict[str, Any], selection: dict[str, Any], proof_id: str, input_fingerprint: str) -> dict[str, Any]:
    fingerprint_payload = _fingerprint_payload(snapshot, selection)
    if _digest(fingerprint_payload) != input_fingerprint:
        raise VideoProofError("Proof job fingerprint does not match its canonical snapshot, selection and renderer recipe.")
    artifacts: dict[str, Any] = {}
    evidence_dir = staging_proof / "evidence"
    for case_id, spec in selection.items():
        filename = {"opening": "opening.mp4", "transition-standard": "transition-standard.mp4", "transition-risk": "transition-risk.mp4", "long-title": "long-title.png", "closing": "closing.mp4", "thumbnail": "thumbnail.png"}[case_id]
        path = staging_proof / filename
        validation = _validate_still(path, 1920, 1080) if case_id == "long-title" else _validate_still(path, 1280, 720) if case_id == "thumbnail" else _validate_video(path, spec, snapshot)
        artifact = {"path": (PROOF_ROOT / proof_id / filename).as_posix(), "kind": spec["kind"], "caseId": case_id, "selection": spec, "validation": validation}
        if path.suffix.casefold() == ".mp4":
            _extract_frame(path, evidence_dir / f"{case_id}-first.png", timestamp=0)
            _extract_frame(path, evidence_dir / f"{case_id}-last.png", timestamp=max(0, float(validation["durationSeconds"]) - 1 / PROOF_FPS))
            artifact["evidenceFrames"] = [(PROOF_ROOT / proof_id / "evidence" / f"{case_id}-first.png").as_posix(), (PROOF_ROOT / proof_id / "evidence" / f"{case_id}-last.png").as_posix()]
        artifacts[case_id] = artifact
    manifest = {
        "schemaVersion": PROOF_SCHEMA_VERSION,
        "proofId": proof_id,
        "proofVersion": PROOF_SCHEMA_VERSION,
        "recipeVersion": PROOF_RECIPE_VERSION,
        "kind": "release-proof-pack",
        "status": "pending",
        "inputFingerprint": input_fingerprint,
        "createdAt": utc_now(),
        "composition": {"id": "AlbumLandscape", "width": PROOF_WIDTH, "height": PROOF_HEIGHT, "fps": PROOF_FPS, "durationInFrames": _timeline_total(snapshot), "durationSeconds": round(_timeline_total(snapshot) / PROOF_FPS, 6)},
        "selection": selection,
        "artifacts": artifacts,
        "provenance": {"projectManifest": snapshot.get("projectManifest"), "configuration": snapshot.get("configuration"), "source": "current validated Final Instrumentals only", "historicalOutputsUsed": False, "separationInvoked": False, "fullAlbumRender": False, "renderer": "shared AlbumLandscape via Remotion video-only render/renderStill plus FFmpeg audio mux", "snapshot": snapshot, "fingerprintPayload": fingerprint_payload},
        "approval": {"status": "pending", "proofId": proof_id, "inputFingerprint": input_fingerprint, "approvedAt": None, "artifactHashes": {}},
    }
    atomic_write_json(staging_proof / "proof-manifest.json", manifest)
    return manifest


def _current_snapshot(project_root: Path) -> tuple[dict[str, Any], str]:
    snapshot = _real_proof_snapshot(project_root)
    selection = _selection_plan(snapshot)
    return snapshot, proof_input_fingerprint(snapshot, selection)


def read_current_proof_pack(project_manifest: str | Path) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    pointer_path = root / PROOF_POINTER
    pointer = read_json(pointer_path, None)
    if not isinstance(pointer, dict) or not pointer.get("proofId"):
        return {"status": "missing", "ready": False, "approval": {"status": "missing"}, "issues": ["No Proof Pack has been generated for the current Project Folder."]}
    proof_id = str(pointer["proofId"])
    proof_dir = root / PROOF_ROOT / proof_id
    manifest = read_json(proof_dir / "proof-manifest.json", None)
    if not isinstance(manifest, dict):
        return {"status": "blocked", "ready": False, "proofId": proof_id, "approval": {"status": "missing"}, "issues": ["The current Proof Pack manifest is missing or unreadable."]}
    issues: list[str] = []
    try:
        _snapshot, current_fingerprint = _current_snapshot(root)
    except (ProjectError, OSError, ValueError) as exc:
        current_fingerprint = None
        issues.append(str(exc))
    stored = str(manifest.get("inputFingerprint") or "")
    approval = dict(manifest.get("approval") or {})
    if pointer.get("schemaVersion") != PROOF_SCHEMA_VERSION or pointer.get("proofVersion") != PROOF_SCHEMA_VERSION or pointer.get("recipeVersion") != PROOF_RECIPE_VERSION:
        issues.append("The current Proof Pack pointer uses an older fingerprint contract. Generate a schema v2 Proof Pack.")
    if pointer.get("inputFingerprint") != stored:
        issues.append("The current Proof Pack pointer does not match its manifest fingerprint.")
    if manifest.get("schemaVersion") != PROOF_SCHEMA_VERSION or manifest.get("proofVersion") != PROOF_SCHEMA_VERSION or manifest.get("recipeVersion") != PROOF_RECIPE_VERSION:
        issues.append("The current Proof Pack uses an older fingerprint contract. Generate a schema v2 Proof Pack.")
    provenance = manifest.get("provenance") if isinstance(manifest.get("provenance"), dict) else {}
    fingerprint_payload = provenance.get("fingerprintPayload")
    if manifest.get("schemaVersion") == PROOF_SCHEMA_VERSION:
        if not isinstance(fingerprint_payload, dict) or _digest(fingerprint_payload) != stored:
            issues.append("The current Proof Pack canonical fingerprint payload is missing or does not match its manifest.")
        elif fingerprint_payload.get("selection") != manifest.get("selection"):
            issues.append("The current Proof Pack selection was changed without regenerating its fingerprint.")
    if current_fingerprint and current_fingerprint != stored:
        approval["status"] = "stale"
        issues.append("Proof approval is stale because a fingerprinted input changed. Generate a new Proof Pack.")
    if approval.get("status") == "approved" and approval.get("inputFingerprint") != stored:
        approval["status"] = "stale"
        issues.append("Proof approval does not match the manifest fingerprint.")
    if approval.get("status") != "approved":
        issues.append("The current Proof Pack is not human-approved.")
    return {"status": "ready" if not issues else "blocked", "ready": not issues, "proofId": proof_id, "proofFolder": str(proof_dir), "manifestPath": str(proof_dir / "proof-manifest.json"), "inputFingerprint": stored, "currentInputFingerprint": current_fingerprint, "approval": approval, "selection": manifest.get("selection", {}), "artifacts": manifest.get("artifacts", {}), "manifest": manifest, "issues": list(dict.fromkeys(issues))}


def require_approved_proof(project_root: Path) -> dict[str, Any]:
    state = read_current_proof_pack(project_root / "project.json")
    if not state.get("ready") or state.get("approval", {}).get("status") != "approved":
        raise VideoProofError("Sustained Video Export is blocked until the current Proof Pack is generated and human-approved: " + "; ".join(state.get("issues", [])))
    return state


def approve_current_proof(project_manifest: str | Path, proof_id: str) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    proof_dir = root / PROOF_ROOT / proof_id
    manifest_path_on_disk = proof_dir / "proof-manifest.json"
    manifest = read_json(manifest_path_on_disk, None)
    if not isinstance(manifest, dict):
        raise VideoProofError("The requested Proof Pack does not exist.")
    _snapshot, current_fingerprint = _current_snapshot(root)
    if current_fingerprint != manifest.get("inputFingerprint"):
        raise VideoProofError("This Proof Pack is stale; generate a new Proof Pack before approval.")
    current_approval = manifest.get("approval") if isinstance(manifest.get("approval"), dict) else {}
    if current_approval.get("status") == "approved" and current_approval.get("inputFingerprint") == current_fingerprint:
        return read_current_proof_pack(manifest_path)
    artifact_hashes = {key: value.get("validation", {}).get("sha256") for key, value in (manifest.get("artifacts") or {}).items() if isinstance(value, dict)}
    manifest["approval"] = {"status": "approved", "proofId": proof_id, "inputFingerprint": current_fingerprint, "approvedAt": utc_now(), "artifactHashes": artifact_hashes}
    manifest["status"] = "approved"
    atomic_write_json(manifest_path_on_disk, manifest)
    atomic_write_json(root / PROOF_POINTER, {"schemaVersion": PROOF_SCHEMA_VERSION, "proofVersion": PROOF_SCHEMA_VERSION, "recipeVersion": PROOF_RECIPE_VERSION, "proofId": proof_id, "inputFingerprint": current_fingerprint, "updatedAt": utc_now()})
    return read_current_proof_pack(manifest_path)


def reject_current_proof(project_manifest: str | Path, proof_id: str, reason: str | None = None) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    manifest_file = manifest_path.parent / PROOF_ROOT / proof_id / "proof-manifest.json"
    manifest = read_json(manifest_file, None)
    if not isinstance(manifest, dict):
        raise VideoProofError("The requested Proof Pack does not exist.")
    manifest["approval"] = {"status": "rejected", "proofId": proof_id, "inputFingerprint": manifest.get("inputFingerprint"), "rejectedAt": utc_now(), "reason": str(reason or "Rejected during human review.")}
    manifest["status"] = "rejected"
    atomic_write_json(manifest_file, manifest)
    return read_current_proof_pack(manifest_path)


def _spawn_proof_job(project_manifest: Path, processes: dict[str, dict[str, Any]], *, synthetic: bool, snapshot: dict[str, Any], selection: dict[str, Any], input_fingerprint: str) -> dict[str, Any]:
    root = project_manifest.parent
    active = _proof_active_job(root)
    if active:
        raise VideoProofError(f"A video or Proof Pack job {active} is already active.")
    job_id = uuid.uuid4().hex
    job_dir = _proof_job_dir(root, job_id)
    staging = job_dir / "staging" / "proof"
    staging.mkdir(parents=True, exist_ok=False)
    specs: list[dict[str, Any]] = []
    for case_id, spec in selection.items():
        filename = {"opening": "opening.mp4", "transition-standard": "transition-standard.mp4", "transition-risk": "transition-risk.mp4", "long-title": "long-title.png", "closing": "closing.mp4", "thumbnail": "thumbnail.png"}[case_id]
        specs.append({"caseId": case_id, "kind": spec["kind"], "outputPath": str(staging / filename), "frame": spec.get("frame"), "startFrame": spec.get("startFrame"), "endFrame": spec.get("endFrame"), "frameCount": spec.get("frameCount"), "durationSeconds": spec.get("durationSeconds")})
    input_path = job_dir / "input.json"
    fingerprint_payload = _fingerprint_payload(snapshot, selection)
    if _digest(fingerprint_payload) != input_fingerprint:
        raise VideoProofError("Proof job could not be created because its fingerprint payload is inconsistent.")
    atomic_write_json(input_path, {"jobId": job_id, "projectFolder": str(root), "snapshot": snapshot, "selection": selection, "fingerprintPayload": fingerprint_payload, "artifacts": specs, "inputFingerprint": input_fingerprint, "synthetic": synthetic})
    status_path = job_dir / "status.json"
    status = _write_status(status_path, {"jobId": job_id, "kind": "synthetic-release-proof-pack" if synthetic else "real-release-proof-pack", "sourceKind": "synthetic" if synthetic else "real", "status": "queued", "stage": "queued", "progress": 0, "message": "Proof Pack queued.", "inputPath": str(input_path), "proofStagingPath": str(staging), "promotedPath": None, "inputFingerprint": input_fingerprint, "proofVersion": PROOF_SCHEMA_VERSION, "recipeVersion": PROOF_RECIPE_VERSION})
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen([_node_path(), str(PROOF_NODE_SCRIPT), str(input_path)], cwd=str(PROOF_NODE_SCRIPT.parent.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    except OSError as exc:
        _write_status(status_path, {**status, "status": "failed", "stage": "failed", "message": str(exc), "error": str(exc)})
        raise VideoProofError("The Proof Pack renderer could not start.") from exc
    record: dict[str, Any] = {"process": process, "statusPath": status_path, "cancelRequested": False, "telemetryStop": threading.Event()}
    processes[job_id] = record
    threading.Thread(target=_monitor_proof_job, args=(root, job_id, processes), daemon=True).start()
    return read_json(status_path, status)


def start_proof_pack(project_manifest: str | Path, processes: dict[str, dict[str, Any]], *, synthetic: bool = False) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    preview = build_proof_preview(manifest_path, synthetic=synthetic)
    return _spawn_proof_job(manifest_path, processes, synthetic=synthetic, snapshot=preview["snapshot"], selection=preview["selection"], input_fingerprint=preview["inputFingerprint"])


def _cleanup_proof_staging(job_dir: Path) -> None:
    shutil.rmtree(job_dir / "staging", ignore_errors=True)


def _monitor_proof_job(project_root: Path, job_id: str, processes: dict[str, dict[str, Any]]) -> None:
    record = processes[job_id]
    status_path = record["statusPath"]
    job_dir = _proof_job_dir(project_root, job_id)
    raw_output: list[str] = []
    try:
        _write_status(status_path, {**read_json(status_path, {}), "status": "running", "stage": "rendering", "progress": 0.02, "message": "Rendering bounded Proof Pack artifacts from AlbumLandscape."})
        if record["process"].stdout is not None:
            for line in record["process"].stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raw_output.append(line.rstrip()[-4000:])
                    continue
                current = read_json(status_path, {})
                _write_status(status_path, {**current, "status": "running", "stage": event.get("stage", current.get("stage")), "progress": float(event.get("progress", current.get("progress", 0))), "message": event.get("message", current.get("message", "Proof Pack rendering.")), "renderer": event})
        return_code = record["process"].wait()
        if record.get("cancelRequested"):
            _cleanup_proof_staging(job_dir)
            _write_status(status_path, {**read_json(status_path, {}), "status": "cancelled", "stage": "cancelled", "message": "Proof Pack cancelled; no folder was promoted."})
            return
        if return_code != 0:
            _cleanup_proof_staging(job_dir)
            _write_status(status_path, {**read_json(status_path, {}), "status": "failed", "stage": "failed", "message": "Proof Pack renderer failed.", "error": "\n".join(raw_output[-8:])[-12000:]})
            return
        input_value = read_json(job_dir / "input.json", None)
        if not isinstance(input_value, dict):
            raise VideoProofError("Proof Pack input snapshot is missing.")
        staging_proof = job_dir / "staging" / "proof"
        manifest = _validate_and_manifest(staging_proof, input_value["snapshot"], input_value["selection"], job_id, str(input_value["inputFingerprint"]))
        final_folder = project_root / PROOF_ROOT / job_id
        final_folder.parent.mkdir(parents=True, exist_ok=True)
        if final_folder.exists():
            raise VideoProofError("The Proof Pack destination already exists.")
        os.replace(staging_proof, final_folder)
        atomic_write_json(project_root / PROOF_POINTER, {"schemaVersion": PROOF_SCHEMA_VERSION, "proofVersion": PROOF_SCHEMA_VERSION, "recipeVersion": PROOF_RECIPE_VERSION, "proofId": job_id, "inputFingerprint": manifest["inputFingerprint"], "updatedAt": utc_now()})
        _write_status(status_path, {**read_json(status_path, {}), "status": "complete", "stage": "complete", "progress": 1, "message": "Proof Pack validated and promoted.", "promotedPath": str(final_folder), "proofManifestPath": str(final_folder / "proof-manifest.json"), "proofId": job_id, "manifest": manifest})
    except Exception as exc:
        _cleanup_proof_staging(job_dir)
        _write_status(status_path, {**read_json(status_path, {}), "status": "failed", "stage": "failed", "message": str(exc), "error": f"{type(exc).__name__}: {exc}"})
    finally:
        record.pop("process", None)
        processes.pop(job_id, None)


def read_proof_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    root = normalized_path(project_manifest).parent
    status = _read_proof_status(root, job_id)
    if status.get("status") in {"queued", "running", "stopping"} and job_id not in processes:
        _cleanup_proof_staging(_proof_job_dir(root, job_id))
        status = _write_status(_proof_status_path(root, job_id), {**status, "status": "interrupted", "stage": "interrupted", "message": "Proof Pack interrupted by backend restart; no partial folder was promoted."})
    return status


def stop_proof_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    root = normalized_path(project_manifest).parent
    status = read_proof_job(project_manifest, job_id, processes)
    if status.get("status") not in {"queued", "running"}:
        return status
    record = processes.get(job_id)
    if not record:
        return read_proof_job(project_manifest, job_id, processes)
    record["cancelRequested"] = True
    process = record.get("process")
    if process and process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.terminate()
    return _write_status(record["statusPath"], {**status, "status": "stopping", "stage": "stopping", "message": "Stopping Proof Pack without promotion."})


def retry_proof_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status = read_proof_job(project_manifest, job_id, processes)
    if status.get("status") not in {"failed", "cancelled", "interrupted"}:
        raise VideoProofError("Only a failed, cancelled or interrupted Proof Pack can be retried.")
    return start_proof_pack(project_manifest, processes, synthetic=status.get("sourceKind") == "synthetic")
