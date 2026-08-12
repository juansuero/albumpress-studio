from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from .json_store import atomic_write_json, read_json
from .projects import MANIFEST_NAME, ProjectError, _probe_with_ffprobe, fingerprint_file, is_inside, load_manifest, normalized_path, open_project_manifest, project_mutation_lock


CopyFile = Callable[[str, str], Any]


def _relative_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name == ".project.lock" or path.name.endswith(".tmp"):
            continue
        files.append(path.relative_to(root))
    return sorted(files, key=lambda item: item.as_posix().casefold())


def _registered_paths(manifest: dict[str, Any], root: Path) -> list[Path]:
    result: set[Path] = set()
    for output in manifest.get("outputs", {}).values() if isinstance(manifest.get("outputs"), dict) else []:
        if isinstance(output, dict) and output.get("path"):
            path = normalized_path(output["path"])
            if is_inside(path, root):
                result.add(path)
        for container_key in ("provenance", "semanticValidation"):
            container = output.get(container_key) if isinstance(output, dict) else None
            records = container.get("returnedOutputs") if isinstance(container, dict) else None
            for record in records if isinstance(records, list) else []:
                if isinstance(record, dict) and record.get("preservedPath"):
                    path = normalized_path(record["preservedPath"])
                    if is_inside(path, root):
                        result.add(path)
    export = manifest.get("export")
    if isinstance(export, dict):
        folder = export.get("destinationFolder")
        if folder:
            folder_path = normalized_path(folder)
            if is_inside(folder_path, root):
                result.update(path for path in folder_path.rglob("*") if path.is_file())
        items = export.get("items")
        for item in items.values() if isinstance(items, dict) else []:
            if isinstance(item, dict):
                for key in ("sourcePath", "destinationPath"):
                    if item.get(key):
                        path = normalized_path(item[key])
                        if is_inside(path, root) and path.is_file():
                            result.add(path)
    return sorted(result, key=lambda item: item.as_posix().casefold())


def _rebase_legacy_manifest_paths(manifest: dict[str, Any], source_root: Path) -> dict[str, Any]:
    rebased = dict(manifest)

    def relative(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        candidate = Path(value)
        if not candidate.is_absolute():
            return value
        try:
            return candidate.resolve(strict=False).relative_to(source_root).as_posix()
        except ValueError:
            return value

    rebased["outputFolder"] = relative(str(rebased.get("outputFolder", ".")))
    rebased["projectFolder"] = relative(str(rebased.get("projectFolder", ".")))
    for output in rebased.get("outputs", {}).values() if isinstance(rebased.get("outputs"), dict) else []:
        if isinstance(output, dict) and output.get("path"):
            output["path"] = relative(output["path"])
        for container_key in ("provenance", "semanticValidation"):
            container = output.get(container_key) if isinstance(output, dict) else None
            records = container.get("returnedOutputs") if isinstance(container, dict) else None
            for record in records if isinstance(records, list) else []:
                if isinstance(record, dict) and record.get("preservedPath"):
                    record["preservedPath"] = relative(record["preservedPath"])
    for task in rebased.get("tasks", {}).values() if isinstance(rebased.get("tasks"), dict) else []:
        records = task.get("returnedOutputs") if isinstance(task, dict) else None
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict) and record.get("preservedPath"):
                record["preservedPath"] = relative(record["preservedPath"])
    export = rebased.get("export")
    if isinstance(export, dict):
        if export.get("destinationFolder"):
            export["destinationFolder"] = relative(export["destinationFolder"])
        for item in export.get("items", {}).values() if isinstance(export.get("items"), dict) else []:
            if isinstance(item, dict):
                for key in ("sourcePath", "destinationPath"):
                    if item.get(key):
                        item[key] = relative(item[key])
    return rebased


def migration_preview(manifest_path: str | Path, destination: str | Path) -> dict[str, Any]:
    source_manifest = normalized_path(manifest_path)
    source_root = source_manifest.parent
    target = normalized_path(destination)
    if target == source_root or is_inside(target, source_root):
        raise ProjectError("The migration destination must be outside the current Project Folder.")
    if not source_manifest.is_file():
        raise ProjectError("The current Project manifest is missing.")
    files = _relative_files(source_root)
    bytes_total = sum((source_root / relative).stat().st_size for relative in files)
    return {
        "sourceProjectFolder": str(source_root),
        "destinationProjectFolder": str(target),
        "destinationExists": target.exists(),
        "destinationEmpty": not target.exists() or not any(target.iterdir()),
        "artifactCount": len(files),
        "bytes": bytes_total,
        "registeredArtifactCount": len(_registered_paths(load_manifest(source_manifest), source_root)),
        "canMigrate": not target.exists() and bool(files),
        "preservationPlan": "Copy every Project Folder file, validate manifest and hashes, atomically promote, retain original.",
    }


def _validate_copy(source_root: Path, stage_root: Path, probe: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    source_manifest_path = source_root / MANIFEST_NAME
    stage_manifest_path = stage_root / MANIFEST_NAME
    if not stage_manifest_path.is_file():
        raise ProjectError("The staged Project manifest is missing.")
    raw_source_manifest = read_json(source_manifest_path, {})
    source_manifest = load_manifest(source_manifest_path)
    raw_stage_manifest = read_json(stage_manifest_path, {})
    if isinstance(raw_stage_manifest, dict) and int(raw_stage_manifest.get("schemaVersion", 0) or 0) < 2:
        atomic_write_json(stage_manifest_path, _rebase_legacy_manifest_paths(raw_stage_manifest, source_root))
    staged_manifest = open_project_manifest(stage_manifest_path)
    if staged_manifest.get("projectId") != source_manifest.get("projectId"):
        raise ProjectError("The staged Project manifest does not belong to the source Project.")
    source_files = _relative_files(source_root)
    staged_files = _relative_files(stage_root)
    if source_files != staged_files:
        raise ProjectError("The staged Project Folder file inventory does not match the source.")
    artifacts: list[dict[str, Any]] = []
    for relative in source_files:
        source_path = source_root / relative
        staged_path = stage_root / relative
        source_hash = fingerprint_file(source_path)
        staged_hash = fingerprint_file(staged_path)
        manifest_schema_migrated = relative == Path(MANIFEST_NAME) and int(raw_source_manifest.get("schemaVersion", 0) or 0) < 2 if isinstance(raw_source_manifest, dict) else False
        if (not manifest_schema_migrated and source_hash != staged_hash) or (not manifest_schema_migrated and source_path.stat().st_size != staged_path.stat().st_size):
            raise ProjectError(f"Migration integrity failed for {relative.as_posix()}.")
        artifacts.append({"path": relative.as_posix(), "bytes": staged_path.stat().st_size, "sha256": staged_hash, "schemaMigrated": manifest_schema_migrated})
    for output in staged_manifest.get("outputs", {}).values() if isinstance(staged_manifest.get("outputs"), dict) else []:
        if not isinstance(output, dict) or not output.get("path") or output.get("status") != "valid":
            continue
        output_path = normalized_path(output["path"])
        if not output_path.is_file() or not is_inside(output_path, stage_root):
            raise ProjectError("A registered valid Output is missing from the staged Project Folder.")
        expected_hash = output.get("fileFingerprint")
        if expected_hash and fingerprint_file(output_path) != expected_hash:
            raise ProjectError("A registered Output fingerprint does not match the staged bytes.")
        metadata = probe(output_path)
        expected_duration = float(output.get("durationSeconds", 0) or 0)
        actual_duration = float(metadata.get("durationSeconds", 0) or 0)
        if expected_duration and abs(actual_duration - expected_duration) > max(0.25, expected_duration * 0.05):
            raise ProjectError("A staged Output FFprobe duration does not match its manifest metadata.")
    return {"artifactCount": len(artifacts), "bytes": sum(item["bytes"] for item in artifacts), "artifacts": artifacts}


def migrate_project(
    manifest_path: str | Path,
    destination: str | Path,
    *,
    copy_file: CopyFile | None = None,
    probe: Callable[[Path], dict[str, Any]] = _probe_with_ffprobe,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    source_manifest = normalized_path(manifest_path)
    source_root = source_manifest.parent
    target = normalized_path(destination)
    if target == source_root or is_inside(target, source_root):
        raise ProjectError("The migration destination must be outside the current Project Folder.")
    if target.exists():
        raise ProjectError("The migration destination already exists; choose an unused Project Folder.")
    if not source_manifest.is_file():
        raise ProjectError("The current Project manifest is missing.")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / f".{target.name}.migration-{uuid.uuid4().hex[:12]}"
    copier = copy_file or shutil.copy2
    copied = 0
    try:
        for relative in _relative_files(source_root):
            if should_cancel and should_cancel():
                raise ProjectError("Migration cancelled before promotion; the original Project Folder is unchanged.")
            source_path = source_root / relative
            staged_path = stage / relative
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            copier(str(source_path), str(staged_path))
            copied += 1
        validation = _validate_copy(source_root, stage, probe)
        if should_cancel and should_cancel():
            raise ProjectError("Migration cancelled after validation; the original Project Folder is unchanged.")
        stage.replace(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "status": "promoted",
        "sourceProjectFolder": str(source_root),
        "destinationProjectFolder": str(target),
        "copiedFiles": copied,
        "artifactCount": validation["artifactCount"],
        "bytes": validation["bytes"],
        "artifacts": validation["artifacts"],
        "originalRetained": source_root.is_dir() and source_manifest.is_file(),
        "current": False,
    }
