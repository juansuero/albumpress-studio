from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .processing import _probe_with_ffprobe, _slug, fingerprint_file
from .projects import ProjectError, is_inside, load_manifest, normalized_path, project_mutation_lock, save_manifest


def _destination(manifest: dict[str, Any], destination: str | None) -> Path:
    source = normalized_path(manifest["sourceFolder"])
    target = normalized_path(destination) if destination else normalized_path(manifest["outputFolder"]) / "final"
    if target == source or is_inside(target, source):
        raise ProjectError("The Final Instrumental destination must be outside the read-only source folder.")
    return target


def build_export_plan(manifest_path: str | Path, destination: str | None = None) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    manifest = load_manifest(path)
    target = _destination(manifest, destination)
    items: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    selections = manifest.get("selections", {})
    outputs = manifest.get("outputs", {})
    for track in manifest.get("tracks", []):
        selection = selections.get(track["trackId"]) if isinstance(selections, dict) else None
        output = outputs.get(selection.get("outputId")) if isinstance(selection, dict) and isinstance(outputs, dict) else None
        status = "valid"
        reason = None
        source_path = None
        if not isinstance(selection, dict):
            status, reason = "missing", "No final Candidate selected."
        elif not isinstance(output, dict) or output.get("status") != "valid":
            status, reason = "invalid", "The selected Output is not registered as valid."
        elif output.get("isPreview"):
            status, reason = "invalid", "Calibration previews cannot be exported as Final Instrumentals."
        elif output.get("semanticStatus") != "confirmed":
            status, reason = "invalid", "The selected Output is awaiting human semantic confirmation."
        else:
            source_path = normalized_path(str(output.get("path", "")))
            try:
                source_path.relative_to(normalized_path(manifest["outputFolder"]))
            except ValueError:
                status, reason = "invalid", "The selected Output is outside the project workspace."
            if status == "valid" and not source_path.is_file():
                status, reason = "invalid", "The selected Output file is missing."
            if status == "valid" and not output.get("fileFingerprint"):
                status, reason = "invalid", "The selected Output has no registered integrity fingerprint."
            if status == "valid" and fingerprint_file(source_path) != output["fileFingerprint"]:
                status, reason = "invalid", "The selected Output failed integrity validation."
            if status == "valid":
                try:
                    _probe_with_ffprobe(source_path)
                except ProjectError as exc:
                    status, reason = "invalid", str(exc)
        filename = f"{int(track['sequence']):02d}_{_slug(track['title'])}_Instrumental.wav"
        item = {"trackId": track["trackId"], "trackTitle": track["title"], "sequence": track["sequence"], "slot": selection.get("slot") if isinstance(selection, dict) else None, "outputId": selection.get("outputId") if isinstance(selection, dict) else None, "status": status, "reason": reason, "sourcePath": str(source_path) if source_path else None, "destinationPath": str(target / filename)}
        items.append(item)
        if status != "valid":
            missing.append({"trackId": track["trackId"], "trackTitle": track["title"], "reason": reason or "Selection is not exportable."})
    return {"ready": not missing and bool(items), "destinationFolder": str(target), "items": items, "missing": missing, "selectionSummary": manifest.get("selectionSummary", "")}


def export_album(manifest_path: str | Path, destination: str | None = None) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    plan = build_export_plan(path, destination)
    if not plan["ready"]:
        raise ProjectError("Export is blocked until every Track has a valid Selection.")
    manifest = load_manifest(path)
    target = Path(plan["destinationFolder"])
    target.mkdir(parents=True, exist_ok=True)
    temporary_root = target / ".stem-comparison-export-tmp" / uuid.uuid4().hex[:12]
    temporary_root.mkdir(parents=True, exist_ok=True)
    previous_items = manifest.get("export", {}).get("items", {}) if isinstance(manifest.get("export"), dict) else {}
    exported_items: dict[str, Any] = {}
    try:
        for item in plan["items"]:
            source = Path(item["sourcePath"])
            target_path = Path(item["destinationPath"])
            previous_path = previous_items.get(item["trackId"], {}).get("destinationPath") if isinstance(previous_items.get(item["trackId"]), dict) else None
            if target_path.exists() and str(target_path) != previous_path:
                stem = target_path.stem
                suffix = target_path.suffix
                index = 2
                while target_path.exists():
                    target_path = target_path.with_name(f"{stem} ({index}){suffix}")
                    index += 1
            staged = temporary_root / target_path.name
            shutil.copyfile(source, staged)
            copied_fingerprint = fingerprint_file(staged)
            source_fingerprint = fingerprint_file(source)
            if copied_fingerprint != source_fingerprint:
                raise ProjectError(f"Copied Output failed verification for {item['trackTitle']}.")
            _probe_with_ffprobe(staged)
            staged.replace(target_path)
            exported_items[item["trackId"]] = {**item, "destinationPath": str(target_path), "fileFingerprint": copied_fingerprint, "exportedAt": datetime.now(timezone.utc).isoformat()}
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        manifest["export"] = {"status": "current", "destinationFolder": str(target), "items": exported_items, "selectionSummary": manifest.get("selectionSummary", ""), "updatedAt": datetime.now(timezone.utc).isoformat()}
        save_manifest(manifest, path.parent)
    return {"status": "current", "destinationFolder": str(target), "items": list(exported_items.values()), "selectionSummary": manifest.get("selectionSummary", "")}
