from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .json_store import atomic_write_json, read_json
from .project_artifact_library import ArtifactLibraryError, ProjectArtifactLibrary
from .projects import ProjectError, normalized_path, utc_now
from .brand import brand_input_props
from .video import _png_dimensions
from .video_render import NODE_SCRIPT, _code_fingerprint, _inside, _node_path, _sha256, _validate_snapshot_current


THUMBNAIL_SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "render-video-thumbnail.mjs"


class VideoPackageError(ProjectError):
    pass


def _validate_package_snapshot_current(root: Path, snapshot: dict[str, Any]) -> None:
    try:
        _validate_snapshot_current(root, snapshot)
        return
    except ProjectError as exc:
        if "video/config.json" not in str(exc):
            raise

    fingerprints = snapshot.get("fingerprints") if isinstance(snapshot.get("fingerprints"), dict) else {}
    project_path = _inside(root, root / "project.json")
    if fingerprints.get("projectManifest") != _sha256(project_path):
        raise VideoPackageError("Render snapshot is stale: project.json changed before packaging.")
    if snapshot.get("codeFingerprint") != _code_fingerprint():
        raise VideoPackageError("Render snapshot is stale: the Remotion composition changed before packaging.")
    for key, asset in (snapshot.get("assets") or {}).items():
        path = _inside(root, root / str(asset.get("relativePath", "")))
        if not path.is_file() or _sha256(path) != asset.get("sha256"):
            raise VideoPackageError(f"Render snapshot is stale: asset {key} changed before packaging.")

    try:
        config = read_json(root / "video" / "config.json", None)
    except json.JSONDecodeError as exc:
        raise VideoPackageError("Render snapshot is stale: video/config.json is unreadable.") from exc
    if not isinstance(config, dict):
        raise VideoPackageError("The current Video configuration is unreadable.")
    props = snapshot.get("props") if isinstance(snapshot.get("props"), dict) else {}
    expected_props = {
        "artist": config.get("artist"),
        "album": config.get("album"),
        "displayFontFamily": (config.get("typography") or {}).get("displayFontFamily"),
        "utilityFontFamily": (config.get("typography") or {}).get("utilityFontFamily"),
        "colors": config.get("colors"),
        "cinematicFinish": config.get("cinematicFinish"),
        "reducedMotion": bool(config.get("reducedMotion", False)),
        "brand": brand_input_props(config.get("brand") or {}),
    }
    for key, value in expected_props.items():
        if props.get(key) != value:
            raise VideoPackageError(f"Render snapshot is stale: visual configuration field {key} changed before packaging.")
    saved_tracks = {str(item.get("trackId")): item for item in config.get("tracks", []) if isinstance(item, dict)}
    for snapshot_track in snapshot.get("tracks", []):
        current = saved_tracks.get(str(snapshot_track.get("trackId")))
        if not current:
            raise VideoPackageError("Render snapshot is stale: a timeline Track is missing before packaging.")
        for key in ("trackId", "title", "outputId", "fileFingerprint", "finalPath"):
            if current.get(key) != snapshot_track.get(key):
                raise VideoPackageError(f"Render snapshot is stale: Track {snapshot_track.get('sequence')} changed before packaging.")
        if abs(float(current.get("durationSeconds", 0)) - float(snapshot_track.get("durationSeconds", 0))) > 1e-6:
            raise VideoPackageError(f"Render snapshot is stale: Track {snapshot_track.get('sequence')} duration changed before packaging.")


def _latest_render_manifest(root: Path, *, ticket_id: str = "ticket-12", expected_kind: str = "synthetic-two-track-boundary-smoke") -> tuple[Path, dict[str, Any]]:
    candidates = []
    for path in (root / "video" / "renders" / ticket_id).glob("v*" + "/render-manifest.json"):
        try:
            value = read_json(path, None)
            version = int(path.parent.name[1:])
        except (ValueError, OSError):
            continue
        if isinstance(value, dict):
            candidates.append((version, path, value))
    if not candidates:
        raise VideoPackageError(f"No completed {ticket_id} render is available for packaging.")
    _, path, value = max(candidates, key=lambda item: item[0])
    if value.get("kind") != expected_kind:
        raise VideoPackageError(f"The available render is not the expected {expected_kind} render.")
    checks = ((value.get("validation") or {}).get("checks") or {})
    if not checks or not all(checks.values()):
        raise VideoPackageError("The available synthetic render has not passed technical validation.")
    return path, value


def _latest_real_render_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    for ticket_id, expected_kind in (("ticket-20-fast", "real-fast-album"), ("ticket-14", "real-album-landscape")):
        try:
            return _latest_render_manifest(root, ticket_id=ticket_id, expected_kind=expected_kind)
        except VideoPackageError:
            continue
    modern = []
    for path in (root / "video" / "releases").glob("*/manifest.json"):
        value = read_json(path, None)
        if isinstance(value, dict) and value.get("kind") == "real-video-release" and isinstance(value.get("snapshot"), dict):
            checks = (value.get("validation") or {}).get("checks") or {}
            if checks and all(checks.values()):
                modern.append((path.stat().st_mtime, path, value))
    if modern:
        _, path, value = max(modern, key=lambda item: item[0])
        return path, value
    raise VideoPackageError("No completed real Fast or Reference render is available for packaging.")


def _chapter_time(seconds: float) -> str:
    total = max(0, int(seconds + 1e-6))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _human_track_title(title: str) -> str:
    cleaned = re.sub(r"^\s*\d{1,3}-\d{1,3}\s+", "", str(title).strip())
    cleaned = re.sub(r"^\s*\d{1,3}\s*-\s*", "", cleaned)
    return re.sub(r"\s*\+\s*", " / ", cleaned).replace("_", " / ").strip()


def _timed_tracks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    fps = int(snapshot["expected"]["fps"])
    cursor = 0.0
    frame_cursor = 0
    timed = []
    for track in snapshot.get("tracks", []):
        duration = float(track["durationSeconds"])
        start_frame = int(track.get("startFrame", frame_cursor))
        frames = max(1, int(track.get("durationInFrames", duration * fps + 0.5)))
        timed.append({
            "trackId": track["trackId"],
            "sequence": int(track["sequence"]),
            "title": _human_track_title(track["title"]),
            "startSeconds": round(start_frame / fps, 6) if "startFrame" in track else round(cursor, 6),
            "durationSeconds": duration,
            "startFrame": start_frame,
            "durationInFrames": frames,
        })
        cursor += duration
        frame_cursor = start_frame + frames
    return timed


def _next_real_package_version(root: Path, render_id: str) -> int:
    package_root = root / "video" / "packages"
    versioned = []
    prefix = f"real-{render_id}-v"
    for manifest_path in package_root.glob(f"{prefix}*/manifest.json"):
        match = re.fullmatch(re.escape(prefix) + r"(\d+)", manifest_path.parent.name)
        if match:
            versioned.append(int(match.group(1)))
    if versioned:
        return max(versioned) + 1
    if any(package_root.glob(f"real-{render_id}-*/manifest.json")):
        return 2
    return 1


def _inside_output(root: Path, relative_path: str) -> Path:
    path = _inside(root, root / relative_path)
    if not path.is_file():
        raise VideoPackageError(f"Package source is missing: {relative_path}")
    return path


def _artifact_record(root: Path, path: Path, relative: str) -> dict[str, Any]:
    return {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _brand_manifest(snapshot: dict[str, Any]) -> dict[str, Any]:
    props = snapshot.get("props") if isinstance(snapshot.get("props"), dict) else {}
    brand = props.get("brand") if isinstance(props.get("brand"), dict) else {"enabled": False}
    return {
        "enabled": bool(brand.get("enabled", False)),
        "profile": brand.get("profile"),
        "revision": brand.get("revision"),
        "timing": {"openingSeconds": brand.get("openingSeconds"), "closingSeconds": brand.get("closingSeconds")},
        "thumbnailStamp": brand.get("thumbnailStamp"),
        "assets": {key: value for key, value in (snapshot.get("assets") or {}).items() if str(key).startswith("brand-")},
        "snapshotRequired": bool(brand.get("enabled", False)),
    }


def _render_thumbnail(input_path: Path) -> None:
    process = subprocess.run([_node_path(), str(THUMBNAIL_SCRIPT), str(input_path)], cwd=str(THUMBNAIL_SCRIPT.parent.parent), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if process.returncode != 0:
        detail = (process.stdout + "\n" + process.stderr).strip()
        raise VideoPackageError("Thumbnail render failed: " + detail[-2000:])


def generate_synthetic_video_package(project_manifest: str | Path, notes: str | None = None) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    render_manifest_path, render_manifest = _latest_render_manifest(root, ticket_id="ticket-12", expected_kind="synthetic-two-track-boundary-smoke")
    snapshot = render_manifest.get("snapshot")
    if not isinstance(snapshot, dict):
        raise VideoPackageError("The render manifest has no immutable snapshot.")
    _validate_snapshot_current(root, snapshot)
    render_relative = render_manifest_path.relative_to(root).as_posix()
    render_relative_output = str(render_manifest.get("outputPath") or "")
    render_path = _inside_output(root, render_relative_output)
    expected_hash = str((render_manifest.get("validation") or {}).get("sha256") or "")
    if expected_hash != _sha256(render_path):
        raise VideoPackageError("The promoted render hash does not match its render manifest.")

    package_id = f"synthetic-{str(render_manifest.get('jobId') or uuid.uuid4().hex[:12])}-{uuid.uuid4().hex[:6]}"
    staging_root = root / ".stem-comparison" / "video-package-jobs" / package_id
    staging_root.mkdir(parents=True, exist_ok=False)
    package_root = root / "video" / "packages" / package_id
    package_root.parent.mkdir(parents=True, exist_ok=True)
    if package_root.exists():
        raise VideoPackageError("The generated package destination already exists.")

    snapshot_input = staging_root / "thumbnail-input.json"
    thumbnail_path = staging_root / "thumbnail.png"
    atomic_write_json(snapshot_input, {"snapshot": snapshot, "outputPath": str(thumbnail_path)})
    _render_thumbnail(snapshot_input)
    if _png_dimensions(thumbnail_path) != (1280, 720):
        raise VideoPackageError("The generated thumbnail is not 1280×720.")
    snapshot_input.unlink()

    album = str(snapshot.get("props", {}).get("album") or "Album")
    artist = str(snapshot.get("props", {}).get("artist") or "Artist")
    timed_tracks = _timed_tracks(snapshot)
    chapters = "\n".join(f"{_chapter_time(float(track['startSeconds']))} {track['title']}" for track in timed_tracks) + "\n"
    description_notes = str(notes if notes is not None else snapshot.get("props", {}).get("descriptionNotes") or "").strip()
    description = f"{artist} — {album}\n\nInstrumental version.\n\nChapters\n{chapters}"
    if description_notes:
        description += "\nNotes\n" + description_notes + "\n"
    else:
        description += "\n"

    shutil.copy2(render_path, staging_root / "album-video.mp4")
    (staging_root / "chapters.txt").write_text(chapters, encoding="utf-8")
    (staging_root / "description.txt").write_text(description, encoding="utf-8")
    shutil.move(str(thumbnail_path), str(staging_root / "thumbnail.png"))
    artifacts = {
        "albumVideo": _artifact_record(root, staging_root / "album-video.mp4", "album-video.mp4"),
        "thumbnail": _artifact_record(root, staging_root / "thumbnail.png", "thumbnail.png"),
        "chapters": _artifact_record(root, staging_root / "chapters.txt", "chapters.txt"),
        "description": _artifact_record(root, staging_root / "description.txt", "description.txt"),
    }
    manifest = {
        "schemaVersion": 1,
        "packageId": package_id,
        "kind": "synthetic-video-package",
        "renderId": render_manifest.get("jobId"),
        "renderManifest": render_relative,
        "renderSha256": expected_hash,
        "codeFingerprint": _code_fingerprint(),
        "settings": snapshot.get("settings", {}),
        "composition": {"id": "AlbumLandscape", "width": 1920, "height": 1080, "fps": 30, "durationSeconds": snapshot["expected"]["durationSeconds"]},
        "assets": {key: value for key, value in snapshot.get("assets", {}).items() if not key.startswith("audio-")},
        "brand": _brand_manifest(snapshot),
        "tracks": timed_tracks,
        "provenance": {"projectManifest": "project.json", "syntheticFixture": True, "source": "Ticket 12 synthetic render only", "historicalOutputsUsed": False},
        "artifacts": artifacts,
        "createdAt": utc_now(),
    }
    atomic_write_json(staging_root / "manifest.json", manifest)
    shutil.move(str(staging_root), str(package_root))
    result = {"ready": True, "status": "ready", "packageId": package_id, "packageFolder": str(package_root), "manifestPath": str(package_root / "manifest.json"), "artifacts": {key: {**value, "path": str(package_root / value["path"])} for key, value in artifacts.items()}, "chapters": chapters, "description": description, "manifest": manifest}
    return result


def generate_real_video_package(project_manifest: str | Path, notes: str | None = None) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    render_manifest_path, render_manifest = _latest_real_render_manifest(root)
    snapshot = render_manifest.get("snapshot")
    if not isinstance(snapshot, dict) or len(snapshot.get("selectionSnapshot") or []) != 10:
        raise VideoPackageError("The real Video Package requires the ten-track immutable render snapshot.")
    _validate_package_snapshot_current(root, snapshot)
    render_relative = render_manifest_path.relative_to(root).as_posix()
    render_path = _inside_output(root, str(render_manifest.get("outputPath") or ""))
    expected_hash = str((render_manifest.get("validation") or {}).get("sha256") or "")
    if expected_hash != _sha256(render_path):
        raise VideoPackageError("The real promoted render hash does not match its render manifest.")

    render_id = str(render_manifest.get("jobId") or uuid.uuid4().hex[:12])
    package_version = _next_real_package_version(root, render_id)
    package_id = f"real-{render_id}-v{package_version}"
    staging_root = root / ".stem-comparison" / "video-package-jobs" / package_id
    staging_root.mkdir(parents=True, exist_ok=False)
    package_root = root / "video" / "packages" / package_id
    package_root.parent.mkdir(parents=True, exist_ok=True)
    if package_root.exists():
        raise VideoPackageError("The generated real package destination already exists.")

    snapshot_input = staging_root / "thumbnail-input.json"
    thumbnail_path = staging_root / "thumbnail.png"
    atomic_write_json(snapshot_input, {"snapshot": snapshot, "outputPath": str(thumbnail_path)})
    _render_thumbnail(snapshot_input)
    if _png_dimensions(thumbnail_path) != (1280, 720):
        raise VideoPackageError("The generated thumbnail is not 1280×720.")
    snapshot_input.unlink()

    props = snapshot.get("props", {})
    album = str(props.get("album") or "Album")
    artist = str(props.get("artist") or "Artist")
    timed_tracks = _timed_tracks(snapshot)
    chapters = "\n".join(f"{_chapter_time(float(track['startSeconds']))} {track['title']}" for track in timed_tracks) + "\n"
    description_notes = str(notes if notes is not None else props.get("descriptionNotes") or "").strip()
    description = f"{artist} — {album}\n\nInstrumental version.\n\nChapters\n{chapters}"
    if description_notes:
        description += "\nNotes\n" + description_notes + "\n"
    else:
        description += "\n"

    shutil.copy2(render_path, staging_root / "album-video.mp4")
    (staging_root / "chapters.txt").write_text(chapters, encoding="utf-8")
    (staging_root / "description.txt").write_text(description, encoding="utf-8")
    shutil.move(str(thumbnail_path), str(staging_root / "thumbnail.png"))
    artifacts = {
        "albumVideo": _artifact_record(root, staging_root / "album-video.mp4", "album-video.mp4"),
        "thumbnail": _artifact_record(root, staging_root / "thumbnail.png", "thumbnail.png"),
        "chapters": _artifact_record(root, staging_root / "chapters.txt", "chapters.txt"),
        "description": _artifact_record(root, staging_root / "description.txt", "description.txt"),
    }
    manifest = {
        "schemaVersion": 1,
        "packageId": package_id,
        "packageVersion": package_version,
        "kind": "real-album-video-package",
        "renderId": render_manifest.get("jobId"),
        "renderManifest": render_relative,
        "renderSha256": expected_hash,
        "codeFingerprint": _code_fingerprint(),
        "settings": snapshot.get("settings", {}),
        "composition": {"id": "AlbumLandscape", "width": 1920, "height": 1080, "fps": 30, "durationSeconds": snapshot["expected"]["durationSeconds"]},
        "assets": snapshot.get("assets", {}),
        "brand": _brand_manifest(snapshot),
        "tracks": timed_tracks,
        "selectionSnapshot": snapshot.get("selectionSnapshot", []),
        "provenance": {"projectManifest": "project.json", "syntheticFixture": False, "source": "10 current validated HQ5 Final Instrumentals", "historicalOutputsUsed": False, "separationInvoked": False},
        "artifacts": artifacts,
        "createdAt": utc_now(),
    }
    atomic_write_json(staging_root / "manifest.json", manifest)
    shutil.move(str(staging_root), str(package_root))
    return {"ready": True, "status": "ready", "packageId": package_id, "packageFolder": str(package_root), "manifestPath": str(package_root / "manifest.json"), "artifacts": {key: {**value, "path": str(package_root / value["path"])} for key, value in artifacts.items()}, "chapters": chapters, "description": description, "manifest": manifest}


def read_current_video_package(project_manifest: str | Path) -> dict[str, Any]:
    root = normalized_path(project_manifest).parent
    package_root = root / "video" / "packages"
    try:
        resolved = ProjectArtifactLibrary(root).resolve_current_release()
        manifest_path = root / str(resolved["manifestPath"])
    except (ArtifactLibraryError, json.JSONDecodeError):
        # Synthetic packages remain isolated test fixtures and have no release pointer.
        synthetic = sorted((path for path in package_root.glob("*/manifest.json") if isinstance(read_json(path, None), dict) and str((read_json(path, {}) or {}).get("kind") or "").startswith("synthetic")), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        if len(synthetic) == 1:
            manifest_path = synthetic[0]
        else:
            manifests = sorted(package_root.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
            if not manifests:
                return {"ready": False, "status": "missing", "packageFolder": str(package_root), "issues": ["No Video Package has been generated."]}
            return {"ready": False, "status": "blocked", "packageFolder": str(package_root), "issues": ["No unambiguous current Video Package is registered."]}
    manifest = read_json(manifest_path, None)
    if not isinstance(manifest, dict):
        return {"ready": False, "status": "blocked", "packageFolder": str(manifest_path.parent), "issues": ["Video Package manifest is unreadable."]}
    artifacts = {}
    issues = []
    render_relative = str(manifest.get("renderManifest") or "")
    try:
        render_path = _inside_output(root, render_relative)
        render_manifest = read_json(render_path, None)
        if not isinstance(render_manifest, dict) or not isinstance(render_manifest.get("snapshot"), dict):
            issues.append("The package render snapshot is unreadable.")
        else:
            _validate_package_snapshot_current(root, render_manifest["snapshot"])
            render_output = _inside_output(root, str(render_manifest.get("outputPath") or ""))
            if _sha256(render_output) != manifest.get("renderSha256"):
                issues.append("The packaged MP4 no longer matches the validated render.")
    except (ProjectError, json.JSONDecodeError) as exc:
        issues.append(str(exc))
    for key, record in (manifest.get("artifacts") or {}).items():
        path = manifest_path.parent / str(record.get("path"))
        artifacts[key] = {**record, "path": str(path)}
        if not path.is_file() or _sha256(path) != record.get("sha256"):
            issues.append(f"Package artifact {key} is missing or changed.")
    return {"ready": not issues, "status": "ready" if not issues else "blocked", "packageId": manifest.get("packageId"), "packageFolder": str(manifest_path.parent), "manifestPath": str(manifest_path), "artifacts": artifacts, "chapters": (manifest_path.parent / "chapters.txt").read_text(encoding="utf-8") if (manifest_path.parent / "chapters.txt").is_file() else "", "description": (manifest_path.parent / "description.txt").read_text(encoding="utf-8") if (manifest_path.parent / "description.txt").is_file() else "", "manifest": manifest, "issues": issues}
