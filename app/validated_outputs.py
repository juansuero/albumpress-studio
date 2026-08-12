from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from .processing import ProcessingError, _probe_output, _probe_with_ffprobe, _slug, cache_identity, fingerprint_file
from .projects import ProjectError, load_manifest, normalized_path, project_mutation_lock, save_manifest, utc_now


CopyFile = Callable[[str | Path, str | Path], Any]
KNOWN_PRODUCT_INVALID_ROOTS: tuple[Path, ...] = ()


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def invalidate_product_outputs(
    manifest_path: str | Path,
    *,
    source_root: str | Path,
    reason: str,
) -> dict[str, Any]:
    """Fail closed for Outputs known to be semantically invalid for the product."""

    path = normalized_path(manifest_path)
    root = normalized_path(source_root)
    invalidated_at = utc_now()
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        invalidated_roots = manifest.setdefault("invalidatedOutputRoots", [])
        root_text = str(root)
        if root_text not in invalidated_roots:
            invalidated_roots.append(root_text)
        invalidated = []
        outputs = manifest.setdefault("outputs", {})
        for output_id, output in outputs.items():
            if not isinstance(output, dict):
                continue
            provenance = output.get("provenance") if isinstance(output.get("provenance"), dict) else {}
            paths = [output.get("path"), provenance.get("sourceOutputPath"), provenance.get("reportPath")]
            if not any(value and _under(normalized_path(str(value)), root) for value in paths):
                continue
            output["status"] = "invalid"
            output["invalidReason"] = reason
            output["invalidatedAt"] = invalidated_at
            output["provenance"] = {**provenance, "productValidity": "invalid", "invalidReason": reason, "invalidatedAt": invalidated_at}
            invalidated.append(output_id)
            task = manifest.get("tasks", {}).get(output.get("taskId")) if isinstance(manifest.get("tasks"), dict) else None
            if isinstance(task, dict):
                task["stage"] = "Invalidated"
                task["error"] = reason
                task["invalidatedAt"] = invalidated_at

        selections = manifest.setdefault("selections", {})
        for track_id, selection in list(selections.items()):
            if isinstance(selection, dict) and selection.get("outputId") in invalidated:
                manifest.setdefault("selectionHistory", []).append({**selection, "invalidatedAt": invalidated_at, "invalidReason": reason})
                selections.pop(track_id, None)
        summary = []
        for track in manifest.get("tracks", []):
            selection = selections.get(track.get("trackId"))
            summary.append(f"{int(track.get('sequence', 0)):02d} — {track.get('title')}: Candidate {selection.get('slot') if selection else 'Not selected'}")
        manifest["selectionSummary"] = "\n".join(summary)
        manifest["export"] = {"status": "invalidated", "reason": reason, "items": {}, "updatedAt": invalidated_at}
        manifest.setdefault("productInvalidations", []).append({"sourceRoot": root_text, "reason": reason, "outputIds": invalidated, "invalidatedAt": invalidated_at})
        save_manifest(manifest, path.parent)
    return manifest


def _report_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ProjectError("The validated-output report is missing.")
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError("The validated-output report is unreadable.") from exc
    return rows


def _matching_report_row(rows: list[dict[str, Any]], candidate_id: str, source: Path, output: Path) -> dict[str, Any]:
    source_key = str(source).casefold()
    output_key = str(output).casefold()
    matches = [
        row
        for row in rows
        if row.get("status") == "complete"
        and row.get("candidateId") == candidate_id
        and str(row.get("source", "")).casefold() == source_key
        and str(row.get("output", "")).casefold() == output_key
    ]
    if not matches:
        raise ProjectError(f"No complete smoke validation matches Candidate {candidate_id} and this source Output.")
    return matches[-1]


def _same_candidate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("type", "engineIdentifier", "technicalIdentifier", "components", "algorithm"))


def import_validated_outputs(
    manifest_path: str | Path,
    *,
    track_id: str,
    slot_outputs: dict[str, str | Path],
    smoke_report_path: str | Path,
    catalogue: dict[str, Any],
    probe: Callable[[Path], dict[str, Any]] = _probe_with_ffprobe,
    copy_file: CopyFile = shutil.copyfile,
) -> dict[str, Any]:
    """Register already validated Outputs without invoking the separator engine.

    The report, current live Candidate catalogue, source fingerprint, FFprobe metadata,
    and copied-file fingerprint must all agree before the manifest is changed.
    """

    if set(slot_outputs) - {"A", "B", "C", "D"} or not slot_outputs:
        raise ProjectError("Provide one or more Outputs for slots A, B, C, or D.")
    if not catalogue.get("live"):
        raise ProjectError("Live Candidate discovery is unavailable; validated Outputs were not imported.")

    path = normalized_path(manifest_path)
    report_path = normalized_path(smoke_report_path)
    rows = _report_rows(report_path)
    invalid_roots = [normalized_path(item) for item in KNOWN_PRODUCT_INVALID_ROOTS]
    live_candidates = {
        str(item.get("candidateId")): item
        for item in catalogue.get("candidates", [])
        if isinstance(item, dict) and item.get("candidateId")
    }
    engine_version = catalogue.get("engine", {}).get("version")
    staging = path.parent / ".stem-comparison" / "tmp" / f"validated-import-{uuid.uuid4().hex[:12]}"
    staged: list[tuple[Path, Path, dict[str, Any], dict[str, Any]]] = []

    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        track = next((item for item in manifest.get("tracks", []) if item.get("trackId") == track_id), None)
        if not isinstance(track, dict):
            raise ProjectError("The requested Track is not in the Album Project.")
        source = normalized_path(str(track.get("sourcePath", "")))
        if not source.is_file():
            raise ProjectError("The source Track is no longer available.")
        if fingerprint_file(source) != track.get("sourceFingerprint"):
            raise ProjectError("The source Track changed after the Album Project was scanned; rescan before importing.")

        selected = {str(item.get("slot")): item for item in manifest.get("candidates", []) if isinstance(item, dict)}
        output_root = path.parent / "outputs" / "album"
        output_root.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        outputs = manifest.setdefault("outputs", {})
        tasks = manifest.setdefault("tasks", {})
        try:
            for slot, input_value in slot_outputs.items():
                candidate = selected.get(slot)
                if not isinstance(candidate, dict):
                    raise ProjectError(f"Candidate slot {slot} is not configured in the Album Project.")
                candidate_id = str(candidate.get("candidateId", ""))
                live = live_candidates.get(candidate_id)
                if not isinstance(live, dict) or not _same_candidate(candidate, live):
                    raise ProjectError(f"Candidate {slot} no longer matches the live installed catalogue.")
                if engine_version and candidate.get("engineVersion") != engine_version:
                    raise ProjectError(f"Candidate {slot} was saved with a different engine version.")

                input_path = normalized_path(input_value)
                if not input_path.is_file():
                    raise ProjectError(f"The validated source Output for Candidate {slot} is missing.")
                manifest_invalid_roots = manifest.get("invalidatedOutputRoots", [])
                blocked_roots = [*invalid_roots, *(normalized_path(item) for item in manifest_invalid_roots if isinstance(item, str))]
                if any(_under(input_path, root) for root in blocked_roots):
                    raise ProjectError("This validated Output source is marked semantically invalid for the product and cannot be imported.")
                report = _matching_report_row(rows, candidate_id, source, input_path)
                source_duration = float(track.get("durationSeconds", 0))
                report_duration = float(report.get("sourceDurationSeconds", 0))
                if abs(report_duration - source_duration) > max(0.25, source_duration * 0.05):
                    raise ProjectError(f"Smoke report duration does not match the Album Project source for Candidate {slot}.")

                destination = output_root / f"{slot}_{int(track['sequence']):02d}_{_slug(str(track['title']))}_Instrumental.wav"
                output_id = f"album:{track_id}:{slot}"
                previous = outputs.get(output_id)
                if destination.exists():
                    if not isinstance(previous, dict) or previous.get("fileFingerprint") != fingerprint_file(destination):
                        raise ProjectError(f"The destination for Candidate {slot} is occupied; refusing to overwrite it.")

                staged_path = staging / destination.name
                copy_file(input_path, staged_path)
                try:
                    metadata = _probe_output(staged_path, source_duration, probe)
                except ProcessingError as exc:
                    raise ProjectError(str(exc)) from exc
                source_output_fingerprint = fingerprint_file(input_path)
                copied_fingerprint = fingerprint_file(staged_path)
                if copied_fingerprint != source_output_fingerprint:
                    raise ProjectError(f"Copied Output integrity failed for Candidate {slot}.")
                if report.get("outputBytes") is not None and int(report["outputBytes"]) != input_path.stat().st_size:
                    raise ProjectError(f"Smoke report byte size does not match Candidate {slot}.")
                if report.get("outputCodec") and report["outputCodec"] != metadata.get("codec"):
                    raise ProjectError(f"Smoke report codec does not match Candidate {slot}.")
                if report.get("outputDurationSeconds") is not None and abs(float(report["outputDurationSeconds"]) - float(metadata["durationSeconds"])) > 0.01:
                    raise ProjectError(f"Smoke report duration does not match Candidate {slot}.")

                output = {
                    "outputId": output_id,
                    "taskId": f"import:{track_id}:{slot}",
                    "trackId": track_id,
                    "slot": slot,
                    "candidateId": candidate_id,
                    "candidate": dict(candidate),
                    "stem": "Instrumental",
                    "path": str(destination),
                    "format": "WAV",
                    "durationSeconds": metadata["durationSeconds"],
                    "sourceDurationSeconds": source_duration,
                    "codec": metadata.get("codec"),
                    "sampleRate": metadata.get("sampleRate"),
                    "channels": metadata.get("channels"),
                    "cacheIdentity": cache_identity(track, candidate, manifest),
                    "fileFingerprint": copied_fingerprint,
                    "validatedAt": utc_now(),
                    "status": "valid",
                    "semanticStatus": "pending",
                    "isPreview": bool(report.get("isPreview")),
                    "previewWindow": report.get("previewWindow"),
                    "semanticValidation": report.get("semanticValidation"),
                    "provenance": {
                        "kind": "imported-existing-cpu-smoke-output",
                        "reportPath": str(report_path),
                        "reportEntry": report,
                        "sourcePath": str(source),
                        "sourceFingerprint": track["sourceFingerprint"],
                        "sourceOutputPath": str(input_path),
                        "sourceOutputFingerprint": source_output_fingerprint,
                        "semanticStatus": "pending",
                    },
                }
                task_id = output["taskId"]
                task = {
                    "taskId": task_id,
                    "kind": "import",
                    "slot": slot,
                    "candidateId": candidate_id,
                    "trackId": track_id,
                    "stage": "Complete",
                    "outputId": output_id,
                    "source": "validated-cpu-smoke-output",
                    "elapsedSeconds": report.get("totalSeconds"),
                    "modelsDownloaded": report.get("modelsDownloaded", []),
                    "memory": report.get("memory"),
                    "updatedAt": utc_now(),
                }
                staged.append((staged_path, destination, output, task))

            for staged_path, destination, output, task in staged:
                staged_path.replace(destination)
                outputs[output["outputId"]] = output
                tasks[task["taskId"]] = task
            save_manifest(manifest, path.parent)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    return manifest
