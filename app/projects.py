from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .json_store import atomic_write_json, read_json


MANIFEST_NAME = "project.json"
MANIFEST_SCHEMA_VERSION = 2
AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".aiff", ".aif"}
_NATURAL_PARTS = re.compile(r"(\d+)")
_MUTATION_GUARD = threading.RLock()
MIN_PROJECT_FREE_SPACE_BYTES = 1024 * 1024


class ProjectError(ValueError):
    """A user-actionable project intake error."""


class ProjectReadOnlyError(ProjectError):
    """A manifest version cannot be safely mutated by this application."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalized_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def windows_music_folder() -> Path:
    """Resolve the Windows Music known folder with a safe portable fallback."""
    if os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(260)
            result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x000D, None, 0, buffer)
            if result == 0 and buffer.value:
                return normalized_path(buffer.value)
        except (AttributeError, OSError):
            pass
    return normalized_path(Path.home() / "Music")


def default_project_library() -> Path:
    configured = os.environ.get("ALBUMPRESS_PROJECT_LIBRARY") or os.environ.get("STEM_COMPARISON_PROJECT_LIBRARY")
    return normalized_path(configured) if configured else windows_music_folder() / "AlbumPress Studio Projects"


def sanitize_project_name(value: str | None, fallback: str = "Album Project") -> str:
    candidate = str(value or "").strip()
    candidate = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "-", candidate)
    candidate = re.sub(r"[. ]+$", "", candidate).strip()
    if not candidate:
        candidate = fallback
    if candidate.casefold() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        candidate = f"{candidate} Project"
    return candidate[:120]


def _collision_free_folder(parent: Path, name: str) -> tuple[Path, bool, int]:
    base = parent / sanitize_project_name(name)
    candidate = base
    index = 1
    while candidate.exists():
        index += 1
        candidate = parent / f"{base.name} ({index})"
    return candidate, candidate != base, index


def resolve_project_creation(
    source_path: str | Path,
    *,
    project_name: str | None = None,
    project_library: str | Path | None = None,
    project_folder: str | Path | None = None,
) -> dict[str, Any]:
    source = normalized_path(source_path)
    if not source.exists() or not source.is_dir():
        raise ProjectError("Choose an existing album folder.")
    fallback_name = source.name or "Album Project"
    name = sanitize_project_name(project_name, fallback_name)
    if project_folder:
        folder = normalized_path(project_folder)
        collision = folder.exists()
        collision_index = 1
    else:
        library = normalized_path(project_library) if project_library else default_project_library()
        folder, collision, collision_index = _collision_free_folder(library, name)
    parent = folder if folder.exists() else folder.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        usage = shutil.disk_usage(parent)
        free_bytes = int(usage.free)
    except OSError as exc:
        raise ProjectError("The Project Folder destination is not accessible.") from exc
    return {
        "sourceFolder": str(source),
        "projectName": name,
        "projectFolder": str(folder),
        "projectLibrary": str(normalized_path(project_library) if project_library else default_project_library()),
        "collision": collision,
        "collisionIndex": collision_index,
        "freeSpaceBytes": free_bytes,
        "requiredFreeSpaceBytes": MIN_PROJECT_FREE_SPACE_BYTES,
        "freeSpaceOk": free_bytes >= MIN_PROJECT_FREE_SPACE_BYTES,
    }


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def default_output_folder(source: Path) -> Path:
    return source.parent / f"{source.name} - AlbumPress Studio"


def validate_folders(source_path: str | Path, output_path: str | Path | None = None) -> tuple[Path, Path]:
    source = normalized_path(source_path)
    if not source.exists() or not source.is_dir():
        raise ProjectError("Choose an existing album folder.")
    output = normalized_path(output_path) if output_path else default_output_folder(source)
    if output == source:
        raise ProjectError("The project output folder must be different from the source folder.")
    if is_inside(output, source):
        raise ProjectError("The project output folder must be outside the read-only source folder.")
    return source, output


def natural_sort_key(name: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in _NATURAL_PARTS.split(name))


def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_with_ffprobe(path: Path) -> dict[str, Any]:
    executable = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    completed = subprocess.run(
        [executable, "-v", "error", "-show_entries", "format=duration:stream=codec_name,sample_rate,channels", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ProjectError((completed.stderr or "FFprobe could not read this audio file.").strip())
    try:
        payload = json.loads(completed.stdout)
        duration = float(payload.get("format", {}).get("duration", 0))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ProjectError("FFprobe returned unreadable audio metadata.") from exc
    if duration <= 0:
        raise ProjectError("The audio file has no usable duration.")
    stream = next((item for item in payload.get("streams", []) if item.get("codec_name")), {})
    return {
        "durationSeconds": round(duration, 6),
        "codec": stream.get("codec_name"),
        "sampleRate": stream.get("sample_rate"),
        "channels": stream.get("channels"),
    }


def scan_tracks(source: Path, *, probe: Any = _probe_with_ffprobe) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    files = sorted((item for item in source.iterdir() if item.is_file()), key=lambda item: natural_sort_key(item.name))
    tracks: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    title_counts: dict[str, int] = {}
    for path in files:
        if path.suffix.casefold() not in AUDIO_EXTENSIONS:
            if path.suffix:
                unsupported.append({"name": path.name, "reason": "Unsupported file type"})
            continue
        try:
            metadata = probe(path)
            source_fingerprint = fingerprint_file(path)
        except (OSError, ProjectError, subprocess.SubprocessError) as exc:
            unsupported.append({"name": path.name, "reason": str(exc) or "Unreadable audio file"})
            continue
        base_title = path.stem
        title_counts[base_title.casefold()] = title_counts.get(base_title.casefold(), 0) + 1
        occurrence = title_counts[base_title.casefold()]
        title = base_title if occurrence == 1 else f"{base_title} ({occurrence})"
        track_id = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:16]
        stat = path.stat()
        tracks.append({
            "trackId": track_id,
            "sourcePath": str(path),
            "title": title,
            "sequence": len(tracks) + 1,
            "extension": path.suffix.casefold(),
            "sizeBytes": stat.st_size,
            "modifiedTime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
            "sourceFingerprint": source_fingerprint,
            **metadata,
        })
    return tracks, unsupported


def _new_manifest(source: Path, output: Path, project_name: str | None = None) -> dict[str, Any]:
    now = utc_now()
    name = sanitize_project_name(project_name, source.name or "Album Project")
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "projectId": hashlib.sha256(str(source).casefold().encode("utf-8")).hexdigest()[:16],
        "projectName": name,
        "albumName": source.name,
        "sourceFolder": str(source),
        "projectFolder": ".",
        "outputFolder": ".",
        "projectPaths": {"outputs": "outputs", "final": "final", "working": ".stem-comparison"},
        "createdAt": now,
        "updatedAt": now,
        "tracks": [],
        "unsupportedFiles": [],
        "candidates": [],
        "outputs": {},
        "tasks": {},
        "selections": {},
        "selectionHistory": [],
        "selectionSummary": "",
        "loops": {},
        "export": {"status": "not-exported"},
        "settings": {"concurrency": 1, "cpuOnly": True},
    }


def migrate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    version = manifest.get("schemaVersion", 0)
    if version > MANIFEST_SCHEMA_VERSION:
        raise ProjectReadOnlyError("This Album Project was created by a newer app version and is read-only here.")
    migrated = dict(manifest)
    if version == 0:
        migrated["schemaVersion"] = MANIFEST_SCHEMA_VERSION
        migrated.setdefault("candidates", [])
        migrated.setdefault("outputs", {})
        migrated.setdefault("tasks", {})
        migrated.setdefault("selections", {})
        migrated.setdefault("selectionHistory", [])
        migrated.setdefault("selectionSummary", "")
        migrated.setdefault("loops", {})
        migrated.setdefault("export", {"status": "not-exported"})
        migrated.setdefault("settings", {"concurrency": 1, "cpuOnly": True})
    if version < MANIFEST_SCHEMA_VERSION:
        migrated["schemaVersion"] = MANIFEST_SCHEMA_VERSION
        migrated.setdefault("projectName", migrated.get("albumName") or "Album Project")
        migrated["projectFolder"] = "."
        migrated["outputFolder"] = "."
        migrated.setdefault("projectPaths", {"outputs": "outputs", "final": "final", "working": ".stem-comparison"})
    return migrated


def _resolve_project_value(value: Any, root: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    return str((root / path).resolve(strict=False)) if not path.is_absolute() else str(path.resolve(strict=False))


def _hydrate_manifest_paths(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    manifest["projectFolder"] = str(root)
    manifest["outputFolder"] = str(root)
    for output in manifest.get("outputs", {}).values() if isinstance(manifest.get("outputs"), dict) else []:
        if isinstance(output, dict) and output.get("path"):
            output["path"] = _resolve_project_value(output["path"], root)
        for container_key in ("provenance", "semanticValidation"):
            container = output.get(container_key)
            if isinstance(container, dict):
                _hydrate_returned_paths(container.get("returnedOutputs"), root)
    for task in manifest.get("tasks", {}).values() if isinstance(manifest.get("tasks"), dict) else []:
        if isinstance(task, dict):
            _hydrate_returned_paths(task.get("returnedOutputs"), root)
    export = manifest.get("export")
    if isinstance(export, dict):
        for key in ("destinationFolder",):
            if export.get(key):
                export[key] = _resolve_project_value(export[key], root)
        for item in export.get("items", {}).values() if isinstance(export.get("items"), dict) else []:
            if isinstance(item, dict):
                for key in ("sourcePath", "destinationPath"):
                    if item.get(key):
                        item[key] = _resolve_project_value(item[key], root)
    return manifest


def _hydrate_returned_paths(records: Any, root: Path) -> None:
    if not isinstance(records, list):
        return
    for record in records:
        if isinstance(record, dict) and record.get("preservedPath"):
            record["preservedPath"] = _resolve_project_value(record["preservedPath"], root)


def _persist_manifest_paths(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    persisted = deepcopy(manifest)
    persisted["schemaVersion"] = MANIFEST_SCHEMA_VERSION
    persisted["projectFolder"] = "."
    persisted["outputFolder"] = "."
    persisted.setdefault("projectPaths", {"outputs": "outputs", "final": "final", "working": ".stem-comparison"})

    def relative(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        candidate = Path(value).resolve(strict=False)
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return value

    for output in persisted.get("outputs", {}).values() if isinstance(persisted.get("outputs"), dict) else []:
        if isinstance(output, dict) and output.get("path"):
            output["path"] = relative(output["path"])
        for container_key in ("provenance", "semanticValidation"):
            container = output.get(container_key)
            if isinstance(container, dict):
                _persist_returned_paths(container.get("returnedOutputs"), relative)
    for task in persisted.get("tasks", {}).values() if isinstance(persisted.get("tasks"), dict) else []:
        if isinstance(task, dict):
            _persist_returned_paths(task.get("returnedOutputs"), relative)
    export = persisted.get("export")
    if isinstance(export, dict):
        if export.get("destinationFolder"):
            export["destinationFolder"] = relative(export["destinationFolder"])
        for item in export.get("items", {}).values() if isinstance(export.get("items"), dict) else []:
            if isinstance(item, dict):
                for key in ("sourcePath", "destinationPath"):
                    if item.get(key):
                        item[key] = relative(item[key])
    return persisted


def _persist_returned_paths(records: Any, relative: Any) -> None:
    if not isinstance(records, list):
        return
    for record in records:
        if isinstance(record, dict) and record.get("preservedPath"):
            record["preservedPath"] = relative(record["preservedPath"])


def load_manifest(path: Path) -> dict[str, Any]:
    path = normalized_path(path)
    raw = read_json(path, None)
    if not isinstance(raw, dict):
        raise ProjectError("The Album Project manifest is missing or unreadable.")
    return _hydrate_manifest_paths(migrate_manifest(raw), path.parent)


@contextmanager
def project_mutation_lock(output: Path) -> Iterator[None]:
    lock_path = output / ".project.lock"
    with _MUTATION_GUARD:
        handle = lock_path.open("a+b")
        try:
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def save_manifest(manifest: dict[str, Any], output: Path) -> Path:
    manifest["updatedAt"] = utc_now()
    path = output / MANIFEST_NAME
    atomic_write_json(path, _persist_manifest_paths(manifest, normalized_path(output)))
    return path


def open_project(source_path: str | Path, output_path: str | Path | None = None, *, project_name: str | None = None, probe: Any = _probe_with_ffprobe) -> dict[str, Any]:
    source, output = validate_folders(source_path, output_path)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / MANIFEST_NAME
    with project_mutation_lock(output):
        manifest = load_manifest(manifest_path) if manifest_path.exists() else _new_manifest(source, output, project_name)
        if normalized_path(manifest.get("sourceFolder", source)) != source:
            raise ProjectError("The selected output folder belongs to a different source Album Project.")
        tracks, unsupported = scan_tracks(source, probe=probe)
        manifest["sourceFolder"] = str(source)
        manifest["outputFolder"] = str(output)
        manifest["projectFolder"] = str(output)
        manifest["projectName"] = sanitize_project_name(project_name or manifest.get("projectName"), source.name or "Album Project")
        manifest["albumName"] = source.name
        manifest["tracks"] = tracks
        manifest["unsupportedFiles"] = unsupported
        manifest["schemaVersion"] = MANIFEST_SCHEMA_VERSION
        save_manifest(manifest, output)
    return manifest


def open_project_manifest(manifest_path: str | Path, *, include_source_scan: bool = True) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    if path.name != MANIFEST_NAME:
        raise ProjectError("The selected file is not an Album Project manifest.")
    raw = read_json(path, None)
    if not isinstance(raw, dict):
        raise ProjectError("The Album Project manifest is missing or unreadable.")
    manifest = load_manifest(path)
    if int(raw.get("schemaVersion", 0) or 0) < MANIFEST_SCHEMA_VERSION:
        with project_mutation_lock(path.parent):
            save_manifest(manifest, path.parent)
            manifest = load_manifest(path)
    if include_source_scan:
        manifest["sourceState"] = source_state(manifest)
    else:
        available = Path(str(manifest.get("sourceFolder", ""))).is_dir()
        manifest["sourceState"] = {"status": "available" if available else "missing", "detail": "Source folder is available." if available else "Source folder is unavailable; relink is available."}
    return manifest


def source_state(manifest: dict[str, Any]) -> dict[str, Any]:
    source = normalized_path(str(manifest.get("sourceFolder", "")))
    if not source.is_dir():
        return {"status": "missing", "detail": "The source folder is unavailable. Compare, Selection, and Export remain available; rescan and processing require relinking."}
    expected = manifest.get("tracks", [])
    try:
        actual, _ = scan_tracks(source)
    except (OSError, ProjectError, subprocess.SubprocessError):
        return {"status": "unreadable", "detail": "The source folder could not be scanned."}
    expected_by_name = {Path(str(item.get("sourcePath", ""))).name.casefold(): item for item in expected if isinstance(item, dict)}
    actual_by_name = {Path(str(item.get("sourcePath", ""))).name.casefold(): item for item in actual}
    if set(expected_by_name) != set(actual_by_name):
        return {"status": "changed", "detail": "The source Track set changed. Rescan is required after reviewing the folder."}
    if any(expected_by_name[name].get("sourceFingerprint") != actual_by_name[name].get("sourceFingerprint") for name in expected_by_name):
        return {"status": "changed", "detail": "One or more source Tracks changed. Rescan is required before processing."}
    return {"status": "available", "detail": "Source folder and Track fingerprints match the project."}


def project_descriptor(manifest_path: str | Path, *, include_source_scan: bool = False) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    manifest = open_project_manifest(path, include_source_scan=include_source_scan)
    state = source_state(manifest) if include_source_scan else {
        "status": "available" if Path(str(manifest.get("sourceFolder", ""))).is_dir() else "missing",
        "detail": "Source folder is available." if Path(str(manifest.get("sourceFolder", ""))).is_dir() else "Source folder is unavailable; relink is available.",
    }
    return {
        "projectId": manifest.get("projectId"),
        "projectName": manifest.get("projectName") or manifest.get("albumName"),
        "albumName": manifest.get("albumName"),
        "projectFolder": str(path.parent),
        "manifestPath": str(path),
        "sourceFolder": manifest.get("sourceFolder"),
        "sourceState": state,
        "updatedAt": manifest.get("updatedAt"),
        "trackCount": len(manifest.get("tracks", [])),
        "selectionCount": len(manifest.get("selections", {})),
    }


def discover_project_manifests(settings: dict[str, Any]) -> dict[str, Any]:
    library = normalized_path(str(settings.get("projectLibrary") or default_project_library()))
    candidates: list[tuple[Path, str]] = []
    if library.is_dir():
        for child in sorted(library.iterdir(), key=lambda item: natural_sort_key(item.name)):
            manifest = child / MANIFEST_NAME
            if manifest.is_file():
                candidates.append((manifest, "library"))
    for recent in settings.get("recentProjects", []) if isinstance(settings.get("recentProjects"), list) else []:
        pointer = recent.get("manifestPath") if isinstance(recent, dict) else recent
        if pointer:
            candidates.append((normalized_path(str(pointer)), "recent"))
    seen: set[str] = set()
    projects: list[dict[str, Any]] = []
    for manifest, origin in candidates:
        key = str(manifest).casefold()
        if key in seen or not manifest.is_file():
            continue
        seen.add(key)
        try:
            descriptor = project_descriptor(manifest)
        except (OSError, ProjectError):
            continue
        descriptor["origin"] = origin
        projects.append(descriptor)
    projects.sort(key=lambda item: (0 if item.get("origin") == "library" else 1, str(item.get("projectName") or "").casefold()))
    return {
        "projectLibrary": str(library),
        "projects": projects,
        "lastProjectManifest": settings.get("lastProjectManifest"),
    }


def remember_project(settings: dict[str, Any], manifest_path: str | Path) -> dict[str, Any]:
    path = str(normalized_path(manifest_path))
    existing = settings.get("recentProjects", [])
    pointers = [item for item in existing if isinstance(item, dict) and str(item.get("manifestPath", "")).casefold() != path.casefold()]
    pointers.insert(0, {"manifestPath": path, "lastOpenedAt": utc_now()})
    settings["recentProjects"] = pointers[:20]
    settings["lastProjectManifest"] = path
    return settings


def remove_recent_project(settings: dict[str, Any], manifest_path: str | Path) -> dict[str, Any]:
    target = str(normalized_path(manifest_path)).casefold()
    settings["recentProjects"] = [
        item for item in settings.get("recentProjects", [])
        if not (isinstance(item, dict) and str(item.get("manifestPath", "")).casefold() == target)
    ]
    return settings


def relink_source(manifest_path: str | Path, source_path: str | Path, *, probe: Any = _probe_with_ffprobe) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    source = normalized_path(source_path)
    if not source.is_dir():
        raise ProjectError("Choose an existing source folder before relinking.")
    manifest = load_manifest(path)
    actual, unsupported = scan_tracks(source, probe=probe)
    expected = manifest.get("tracks", [])
    expected_by_name = {Path(str(item.get("sourcePath", ""))).name.casefold(): item for item in expected if isinstance(item, dict)}
    actual_by_name = {Path(str(item.get("sourcePath", ""))).name.casefold(): item for item in actual}
    if set(expected_by_name) != set(actual_by_name):
        raise ProjectError("The replacement folder does not contain the exact expected Track set.")
    for name, expected_track in expected_by_name.items():
        if expected_track.get("sourceFingerprint") != actual_by_name[name].get("sourceFingerprint"):
            raise ProjectError(f"The replacement Track does not match the expected fingerprint: {name}")
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        manifest["sourceFolder"] = str(source)
        manifest["tracks"] = actual
        manifest["unsupportedFiles"] = unsupported
        manifest["sourceRelink"] = {"status": "verified", "verifiedAt": utc_now()}
        save_manifest(manifest, path.parent)
    manifest["sourceState"] = {"status": "available", "detail": "Source folder and Track fingerprints match the project."}
    return manifest


def create_project(
    source_path: str | Path,
    *,
    project_name: str | None = None,
    project_library: str | Path | None = None,
    project_folder: str | Path | None = None,
    probe: Any = _probe_with_ffprobe,
) -> dict[str, Any]:
    plan = resolve_project_creation(source_path, project_name=project_name, project_library=project_library, project_folder=project_folder)
    folder = Path(plan["projectFolder"])
    if not plan["freeSpaceOk"]:
        raise ProjectError("The Project Folder destination does not have enough free space for project state.")
    if folder.exists() and (folder / MANIFEST_NAME).exists():
        manifest = open_project_manifest(folder / MANIFEST_NAME)
        if normalized_path(str(manifest.get("sourceFolder", ""))) != normalized_path(source_path):
            raise ProjectError("The selected Project Folder belongs to a different source Album Project.")
        return manifest
    if folder.exists() and any(folder.iterdir()):
        raise ProjectError("The selected Project Folder is not empty; choose another destination.")
    return open_project(source_path, folder, project_name=plan["projectName"], probe=probe)


def rescan_project(manifest_path: str | Path, *, probe: Any = _probe_with_ffprobe) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    if path.name != MANIFEST_NAME:
        raise ProjectError("The selected file is not an Album Project manifest.")
    manifest = load_manifest(path)
    if not Path(str(manifest.get("sourceFolder", ""))).is_dir():
        raise ProjectError("The source folder is unavailable. Locate and verify it before rescanning.")
    return open_project(manifest["sourceFolder"], manifest["outputFolder"], probe=probe)


def choose_folder() -> str | None:
    if os.name != "nt":
        return None
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Choose album folder")
        root.destroy()
        return str(normalized_path(selected)) if selected else None
    except Exception:
        return None
