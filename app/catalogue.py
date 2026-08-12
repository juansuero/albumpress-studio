from __future__ import annotations

import importlib.metadata
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import AppPaths, ensure_app_paths
from .json_store import atomic_write_json, read_json
from .projects import ProjectError, load_manifest, normalized_path, project_mutation_lock, save_manifest


FAST_MODEL_FILENAME = "UVR-MDX-NET-Inst_HQ_5.onnx"
FAST_CANDIDATE_ID = f"model:{FAST_MODEL_FILENAME}"
RECOMMENDED_PRESETS = ("instrumental_full", "instrumental_balanced", "instrumental_clean")
LOCAL_FAST_BENCHMARK = {
    "benchmarkId": "uvr-mdx-hq5-cpu-2026-08-09",
    "inputSeconds": 45.0,
    "separationSeconds": 91.533,
    "wallClockSeconds": 138.365,
    "modelLoadSeconds": 26.617,
    "secondsPerSourceSecond": round(91.533 / 45.0, 6),
    "evidence": "Local benchmark; source audio and raw run metadata are not distributed.",
}
CATALOGUE_SNAPSHOT = "catalogue.json"


def generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _version() -> str | None:
    try:
        return importlib.metadata.version("audio-separator")
    except importlib.metadata.PackageNotFoundError:
        return None


def _cache_state(files: list[str], model_cache: Path) -> str:
    if not files:
        return "Unknown"
    names = [Path(item.split("?", 1)[0]).name for item in files]
    return "Cached" if all((model_cache / name).is_file() for name in names) else "Download required"


def _normalise_models(grouped_models: dict[str, Any], model_cache: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for group, entries in grouped_models.items():
        if not isinstance(entries, dict):
            continue
        for readable_name, metadata in entries.items():
            if not isinstance(metadata, dict) or not metadata.get("filename"):
                continue
            filename = str(metadata["filename"])
            candidate = {
                "candidateId": f"model:{filename}",
                "type": "Model",
                "label": str(readable_name),
                "engineIdentifier": filename,
                "technicalIdentifier": filename,
                "group": str(group),
                "components": [],
                "algorithm": None,
                "stems": metadata.get("stems", []),
                "targetStem": metadata.get("target_stem"),
                "metadata": metadata,
                "cacheState": _cache_state([str(item) for item in metadata.get("download_files", [])], model_cache),
            }
            if filename.casefold() == FAST_MODEL_FILENAME.casefold():
                candidate.update({
                    "processingProfile": "fast",
                    "defaultSlot": "A",
                    "reusableLoadedModel": True,
                    "benchmark": LOCAL_FAST_BENCHMARK,
                })
            candidates.append(candidate)
    return candidates


def _normalise_presets(presets: dict[str, Any], model_cache: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for preset_id, metadata in presets.items():
        if not isinstance(metadata, dict):
            continue
        models = [str(item) for item in metadata.get("models", [])]
        profile = "deep" if preset_id == "instrumental_full" else "slow"
        candidates.append({
            "candidateId": f"preset:{preset_id}",
            "type": "Preset",
            "label": str(metadata.get("name") or preset_id),
            "engineIdentifier": str(preset_id),
            "technicalIdentifier": str(preset_id),
            "group": "Preset",
            "components": models,
            "algorithm": metadata.get("algorithm"),
            "stems": [],
            "targetStem": "instrumental" if preset_id.startswith("instrumental_") else None,
            "metadata": metadata,
            "cacheState": _cache_state(models, model_cache),
            "processingProfile": profile,
            "onDemand": profile != "fast",
        })
    return candidates


def _separator_factory(model_cache: Path) -> Any:
    from audio_separator.separator import Separator

    return Separator(info_only=True, model_file_dir=str(model_cache))


def discover_catalogue(
    *,
    paths: AppPaths | None = None,
    separator_factory: Callable[[Path], Any] = _separator_factory,
) -> dict[str, Any]:
    resolved = paths or ensure_app_paths()
    now = generated_at()
    try:
        separator = separator_factory(resolved.model_cache)
        models = _normalise_models(separator.list_supported_model_files(), resolved.model_cache)
        presets = _normalise_presets(separator.list_ensemble_presets(), resolved.model_cache)
        candidates = presets + models
        by_id = {candidate["candidateId"]: candidate for candidate in candidates}
        recommendations = []
        recommendation_ids = [FAST_CANDIDATE_ID, *(f"preset:{preset_id}" for preset_id in RECOMMENDED_PRESETS)]
        for candidate_id in recommendation_ids:
            candidate = by_id.get(candidate_id)
            recommendations.append({
                "candidateId": candidate_id,
                "available": candidate is not None,
                "candidate": candidate,
                "reason": None if candidate else "This Candidate is not exposed by the installed engine.",
            })
        snapshot = {
            "live": True,
            "status": "current",
            "engine": {"name": "audio-separator", "version": _version()},
            "generatedAt": now,
            "recommendations": recommendations,
            "candidates": candidates,
            "counts": {"models": len(models), "presets": len(presets), "total": len(candidates)},
            "error": None,
        }
        atomic_write_json(resolved.data_dir / CATALOGUE_SNAPSHOT, snapshot)
        return snapshot
    except Exception as exc:
        stale = read_json(resolved.data_dir / CATALOGUE_SNAPSHOT, None)
        return {
            "live": False,
            "status": "stale" if isinstance(stale, dict) else "unavailable",
            "engine": {"name": "audio-separator", "version": _version()},
            "generatedAt": now,
            "recommendations": [],
            "candidates": [],
            "counts": {"models": 0, "presets": 0, "total": 0},
            "error": str(exc),
            "staleSnapshot": stale if isinstance(stale, dict) else None,
        }


def estimate_candidate_seconds(candidate: dict[str, Any], duration_seconds: float, *, cold_start: bool = True) -> float | None:
    benchmark = candidate.get("benchmark")
    if not isinstance(benchmark, dict):
        return None
    try:
        estimate = max(0.0, float(duration_seconds)) * float(benchmark["secondsPerSourceSecond"])
        if cold_start:
            estimate += float(benchmark.get("modelLoadSeconds", 0))
        return round(estimate, 1)
    except (KeyError, TypeError, ValueError):
        return None


def apply_fast_default_slots(manifest_path: str | Path, catalogue: dict[str, Any]) -> dict[str, Any]:
    """Set Fast as A while retaining historical Outputs as immutable invalid evidence."""

    fast = next((item for item in catalogue.get("candidates", []) if isinstance(item, dict) and item.get("candidateId") == FAST_CANDIDATE_ID), None)
    if not isinstance(fast, dict):
        raise ProjectError("The installed engine does not expose the HQ5 Fast Candidate.")
    path = normalized_path(manifest_path)
    now = generated_at()
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        selected = {"A": {**fast, "slot": "A", "engineVersion": catalogue.get("engine", {}).get("version")}}
        old_candidates = {str(item.get("slot")): item for item in manifest.get("candidates", []) if isinstance(item, dict)}
        old_by_slot = {slot: item.get("candidateId") for slot, item in old_candidates.items()}
        manifest["candidates"] = [selected["A"]]
        stale_output_ids: list[str] = []
        outputs = manifest.setdefault("outputs", {})
        for output_id, output in outputs.items():
            if not isinstance(output, dict):
                continue
            slot = str(output.get("slot", ""))
            if slot == "A" and output.get("candidateId") == FAST_CANDIDATE_ID:
                continue
            if output.get("status") == "valid":
                output["status"] = "invalid"
                output["invalidReason"] = "Candidate configuration changed; reprocess under the selected Fast/Deep route."
                output["invalidatedAt"] = now
                output.setdefault("provenance", {})["productValidity"] = "invalid"
                output["provenance"]["invalidReason"] = output["invalidReason"]
                stale_output_ids.append(str(output_id))
        selections = manifest.setdefault("selections", {})
        for track_id, selection in list(selections.items()):
            if not isinstance(selection, dict) or selection.get("outputId") in stale_output_ids or selection.get("slot") != "A":
                manifest.setdefault("selectionHistory", []).append({**selection, "invalidatedAt": now, "invalidReason": "Fast Candidate defaults changed."} if isinstance(selection, dict) else {"trackId": track_id, "invalidatedAt": now})
                selections.pop(track_id, None)
        manifest["selectionSummary"] = "\n".join(f"{int(track.get('sequence', 0)):02d} — {track.get('title')}: Candidate {selections.get(track.get('trackId'), {}).get('slot', 'Not selected')}" for track in manifest.get("tracks", []))
        manifest["fastPath"] = {
            "defaultSlot": "A",
            "candidateId": FAST_CANDIDATE_ID,
            "updatedAt": now,
            "previousSlots": old_by_slot,
            "deepCandidateId": "preset:instrumental_full",
            "catalogueOnlyCandidates": ["preset:instrumental_balanced", "preset:instrumental_clean"],
        }
        if stale_output_ids:
            manifest["export"] = {"status": "invalidated", "reason": "Candidate defaults changed; previous Outputs require reprocessing.", "items": {}, "updatedAt": now}
        save_manifest(manifest, path.parent)
    return manifest


def save_candidate_slots(
    manifest_path: str | Path,
    slots: dict[str, dict[str, Any] | None],
    catalogue: dict[str, Any],
) -> dict[str, Any]:
    allowed = {"A", "B", "C", "D"}
    if set(slots) - allowed:
        raise ProjectError("Candidate slots must be A, B, C, or D.")
    catalogue_ids = {candidate.get("candidateId") for candidate in catalogue.get("candidates", [])}
    selected: list[dict[str, Any]] = []
    normalized: dict[str, dict[str, Any] | None] = {}
    for slot in ("A", "B", "C", "D"):
        candidate = slots.get(slot)
        if candidate is None:
            normalized[slot] = None
            continue
        candidate_id = candidate.get("candidateId")
        if candidate_id not in catalogue_ids:
            raise ProjectError("Every Candidate must come from the current installed catalogue.")
        if any(item["candidateId"] == candidate_id for item in selected):
            raise ProjectError("Duplicate Candidate configurations are not allowed.")
        selected.append(candidate)
        normalized[slot] = {**candidate, "slot": slot, "engineVersion": catalogue.get("engine", {}).get("version")}
    if len(selected) < 1:
        raise ProjectError("Choose at least one Candidate before saving.")
    path = normalized_path(manifest_path)
    manifest = load_manifest(path)
    with project_mutation_lock(path.parent):
        manifest["candidates"] = [normalized[slot] for slot in ("A", "B", "C", "D") if normalized[slot] is not None]
        save_manifest(manifest, path.parent)
    return manifest
