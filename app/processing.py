from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import traceback
import unicodedata
import uuid
from datetime import datetime, timezone
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from typing import Any, Callable, Protocol

from .catalogue import estimate_candidate_seconds
from .json_store import atomic_write_json, read_json
from .projects import ProjectError, _probe_with_ffprobe, fingerprint_file, load_manifest, normalized_path, project_mutation_lock, save_manifest


TERMINAL_JOB_STATUSES = {"complete", "failed", "stopped"}
TASK_STAGES = {"Queued", "Downloading model", "Processing", "Validating", "Complete", "Failed", "Skipped"}
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024


class ProcessingError(ValueError):
    """A user-actionable calibration error."""


class SeparatorContract(Protocol):
    def load_model(self, model_filename: str | list[str] | None = None) -> None: ...

    def separate(self, audio_file_path: str, custom_output_names: dict[str, str] | None = None) -> list[str]: ...


SeparatorFactory = Callable[[dict[str, Any], Path, Path], SeparatorContract]
Probe = Callable[[Path], dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def cache_identity(track: dict[str, Any], candidate: dict[str, Any], manifest: dict[str, Any]) -> str:
    payload = {
        "sourceFingerprint": track.get("sourceFingerprint"),
        "engineVersion": candidate.get("engineVersion"),
        "candidate": {
            "type": candidate.get("type"),
            "engineIdentifier": candidate.get("engineIdentifier"),
            "technicalIdentifier": candidate.get("technicalIdentifier"),
            "components": candidate.get("components", []),
            "algorithm": candidate.get("algorithm"),
        },
        "settings": {
            "cpuOnly": manifest.get("settings", {}).get("cpuOnly", True),
            "outputFormat": "WAV",
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def output_cache_hit(output: dict[str, Any] | None, expected_identity: str) -> bool:
    if not isinstance(output, dict) or output.get("status") != "valid" or output.get("isPreview") or output.get("cacheIdentity") != expected_identity:
        return False
    path = Path(str(output.get("path", "")))
    if not path.is_file() or output.get("fileFingerprint") is None:
        return False
    try:
        return fingerprint_file(path) == output.get("fileFingerprint")
    except OSError:
        return False


def disk_space_check(manifest_path: str | Path, *, minimum_free_bytes: int = MIN_FREE_BYTES) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    try:
        free = shutil.disk_usage(path.parent).free
    except OSError as exc:
        return {"ready": False, "detail": f"Could not inspect free disk space: {exc}", "freeBytes": None}
    if free < minimum_free_bytes:
        return {"ready": False, "detail": "Free disk space is materially low; processing is paused to protect the source and existing Outputs.", "freeBytes": free}
    return {"ready": True, "detail": "Free disk space check passed.", "freeBytes": free}


def _slug(value: str, fallback: str = "track") -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-.")
    return slug[:64] or fallback


def _component_config_name(component: str, model_cache: Path) -> str | None:
    direct_names = [
        f"config_{Path(component).stem}.yaml",
        f"{Path(component).stem}.yaml",
    ]
    for name in direct_names:
        if (model_cache / name).is_file():
            return name
    def visit(value: Any) -> str | None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).casefold() == component.casefold() and isinstance(child, str) and child.casefold().endswith(".yaml"):
                    name = Path(child).name
                    return name if (model_cache / name).is_file() else None
                found = visit(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = visit(child)
                if found:
                    return found
        return None

    source_paths = [model_cache / "download_checks.json"]
    try:
        import audio_separator

        source_paths.append(Path(str(audio_separator.__file__)).parent / "models.json")
    except (ImportError, TypeError):
        pass
    for source_path in source_paths:
        if not source_path.is_file():
            continue
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        found = visit(payload)
        if found:
            return found
    return None


def _configured_stem_contract(candidate: dict[str, Any], model_cache: Path) -> dict[str, Any] | None:
    components = [str(item) for item in candidate.get("components", []) if str(item)]
    if not components and candidate.get("engineIdentifier"):
        components = [str(candidate["engineIdentifier"])]
    configured: list[dict[str, Any]] = []
    for component in components:
        config_name = _component_config_name(component, model_cache)
        if not config_name:
            continue
        try:
            import yaml

            config = yaml.load((model_cache / config_name).read_text(encoding="utf-8"), Loader=yaml.FullLoader)
        except (OSError, TypeError, ValueError):
            continue
        training = config.get("training", {}) if isinstance(config, dict) else {}
        instruments = training.get("instruments", []) if isinstance(training, dict) else []
        target = training.get("target_instrument") if isinstance(training, dict) else None
        if isinstance(target, str) and isinstance(instruments, list):
            configured.append({"component": component, "config": config_name, "target": target, "instruments": [str(item) for item in instruments]})
    if not configured:
        return None
    target_names = {_canonical_stem_name(item["target"]) for item in configured}
    if len(target_names) != 1 or None in target_names:
        raise ProcessingError("The selected Candidate's model configs disagree about the target stem.")
    expected_names = {_canonical_stem_name(stem) for item in configured for stem in item["instruments"]}
    if None in expected_names:
        raise ProcessingError("The selected Candidate's model config exposes an unknown stem name.")
    target_name = next(iter(target_names))
    if target_name != _canonical_stem_name(str(candidate.get("targetStem") or target_name)):
        raise ProcessingError("The live Candidate target stem disagrees with its cached model configs.")
    return {
        "targetStem": target_name,
        "expectedStemNames": sorted(expected_names),
        "configuredModels": configured,
        "source": "cached-model-config",
    }


def _production_separator_factory(candidate: dict[str, Any], model_cache: Path, output_dir: Path) -> SeparatorContract:
    from audio_separator.separator import Separator

    kwargs: dict[str, Any] = {
        "model_file_dir": str(model_cache),
        "output_dir": str(output_dir),
        "output_format": "WAV",
        "use_autocast": False,
        "use_directml": False,
    }
    if candidate.get("type") == "Preset":
        kwargs["ensemble_preset"] = candidate["engineIdentifier"]
    separator = Separator(**kwargs)
    if candidate.get("type") == "Model":
        separator.load_model(candidate["engineIdentifier"])
    else:
        separator.load_model()
    if candidate.get("processingProfile") == "fast" and list(getattr(separator, "onnx_execution_provider", []) or []) != ["CPUExecutionProvider"]:
        raise ProcessingError("The Fast Candidate must run with ONNX Runtime CPUExecutionProvider.")
    contract = _configured_stem_contract(candidate, model_cache)
    if contract is None and candidate.get("type") == "Preset":
        raise ProcessingError("The selected Preset has no readable cached model stem contract.")
    if contract is None:
        target_stem = _canonical_stem_name(str(candidate.get("targetStem") or ""))
        contract = {"targetStem": target_stem, "expectedStemNames": [target_stem, "Vocals"], "source": "candidate-declared"} if target_stem else None
    if contract:
        # The engine's output labels are only accepted after they are checked
        # against the live Candidate's declared target and its complement.
        # This metadata prevents the caller from ranking arbitrary filenames.
        setattr(separator, "stem_output_contract", contract)
    return separator


def _prepare_separator_output_dir(separator: SeparatorContract, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(separator, "output_dir"):
        setattr(separator, "output_dir", output_dir)
    model_instance = getattr(separator, "model_instance", None)
    if model_instance is not None and hasattr(model_instance, "output_dir"):
        model_instance.output_dir = output_dir


def _task_separator(
    factory: SeparatorFactory,
    candidate: dict[str, Any],
    model_cache: Path,
    output_dir: Path,
    separator_cache: dict[str, SeparatorContract] | None,
) -> SeparatorContract:
    candidate_key = str(candidate.get("candidateId") or candidate.get("technicalIdentifier") or "")
    reusable = bool(candidate.get("reusableLoadedModel")) and separator_cache is not None and candidate_key
    if reusable and candidate_key not in separator_cache:
        separator_cache[candidate_key] = factory(candidate, model_cache, output_dir)
    separator = separator_cache[candidate_key] if reusable else factory(candidate, model_cache, output_dir)
    _prepare_separator_output_dir(separator, output_dir)
    return separator


def _prepare_preview_input(source: Path, task_dir: Path, input_window: dict[str, Any] | None, probe: Probe) -> tuple[Path, float]:
    if not input_window:
        metadata = probe(source)
        return source, float(metadata.get("durationSeconds", 0))
    try:
        start = float(input_window["startSeconds"])
        duration = float(input_window["durationSeconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProcessingError("Preview start and duration must be numeric seconds.") from exc
    if duration < 30 or duration > 60:
        raise ProcessingError("Preview duration must be between 30 and 60 seconds.")
    if start < 0:
        raise ProcessingError("Preview start must not be negative.")
    clip = task_dir / "preview-input.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(duration), "-i", str(source), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(clip)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        raise ProcessingError(f"Could not create the temporary preview clip: {detail}") from exc
    metadata = probe(clip)
    actual_duration = float(metadata.get("durationSeconds", 0))
    if actual_duration < 30 or actual_duration > 60:
        raise ProcessingError(f"The temporary preview clip has an invalid duration: {actual_duration:.3f}s.")
    return clip, actual_duration


def _job_root(manifest_path: Path) -> Path:
    return manifest_path.parent / ".stem-comparison" / "jobs"


def _state_path(manifest_path: Path, job_id: str) -> Path:
    return _job_root(manifest_path) / job_id / "status.json"


def _read_state(path: Path) -> dict[str, Any]:
    state = read_json(path, None)
    if not isinstance(state, dict):
        raise ProcessingError("The calibration job state is missing or unreadable.")
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    atomic_write_json(path, state)


def _event(state: dict[str, Any], stage: str, message: str, *, task_id: str | None = None, slot: str | None = None) -> None:
    events = state.setdefault("events", [])
    events.append({
        "eventId": len(events) + 1,
        "at": utc_now(),
        "stage": stage,
        "message": message,
        "taskId": task_id,
        "slot": slot,
    })
    if len(events) > 250:
        del events[:-250]


def _update_state(
    path: Path,
    *,
    status: str | None = None,
    stage: str | None = None,
    message: str | None = None,
    task_id: str | None = None,
    slot: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    state = _read_state(path)
    if status is not None:
        state["status"] = status
    if stage is not None:
        state["stage"] = stage
    if values:
        state.update(values)
    if message:
        _event(state, stage or state.get("stage", "Update"), message, task_id=task_id, slot=slot)
    _write_state(path, state)
    return state


def _update_task(path: Path, task_id: str, *, stage: str, started_at: str | None = None, finished_at: str | None = None, elapsed_seconds: float | None = None, error: str | None = None, technical_error: str | None = None, output_id: str | None = None) -> dict[str, Any]:
    state = _read_state(path)
    for task in state.get("tasks", []):
        if task.get("taskId") != task_id:
            continue
        task["stage"] = stage
        if started_at is not None:
            task["startedAt"] = started_at
        if finished_at is not None:
            task["finishedAt"] = finished_at
        if elapsed_seconds is not None:
            task["elapsedSeconds"] = round(elapsed_seconds, 3)
        if error is not None:
            task["error"] = error
        if technical_error is not None:
            task["technicalError"] = technical_error
        if output_id is not None:
            task["outputId"] = output_id
        break
    state["stage"] = stage
    _write_state(path, state)
    return state


def _selected_candidates(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("slot") in {"A", "B", "C", "D"}]


def _find_track(manifest: dict[str, Any], track_id: str | None) -> dict[str, Any]:
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ProcessingError("Choose an Album Project with at least one detected Track first.")
    if track_id is None:
        return tracks[0]
    for track in tracks:
        if track.get("trackId") == track_id:
            return track
    raise ProcessingError("The selected calibration Track is no longer in the Album Project.")


def _ensure_sources_available(manifest: dict[str, Any]) -> None:
    missing = [str(track.get("title")) for track in manifest.get("tracks", []) if not Path(str(track.get("sourcePath", ""))).is_file()]
    if missing:
        raise ProcessingError("The source Album is unavailable; locate and fingerprint-verify it before processing.")


def _normalise_input_window(track: dict[str, Any], start_seconds: float | None, duration_seconds: float | None) -> dict[str, float] | None:
    if start_seconds is None and duration_seconds is None:
        return None
    if start_seconds is None:
        start_seconds = 0.0
    if duration_seconds is None:
        duration_seconds = 45.0
    try:
        start = float(start_seconds)
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ProcessingError("Preview start and duration must be numeric seconds.") from exc
    source_duration = float(track.get("durationSeconds", 0))
    if duration < 30 or duration > 60:
        raise ProcessingError("Preview duration must be between 30 and 60 seconds.")
    if start < 0 or start >= source_duration:
        raise ProcessingError("Preview start must be inside the Track duration.")
    if start + duration > source_duration:
        raise ProcessingError("Preview window must fit inside the selected Track.")
    return {"startSeconds": start, "durationSeconds": duration}


def create_calibration_job(
    manifest_path: str | Path,
    track_id: str | None = None,
    *,
    preview_start_seconds: float | None = None,
    preview_duration_seconds: float | None = None,
) -> tuple[str, Path, dict[str, Any]]:
    path = normalized_path(manifest_path)
    manifest = load_manifest(path)
    _ensure_sources_available(manifest)
    candidates = _selected_candidates(manifest)
    if not candidates:
        raise ProcessingError("Choose at least one Candidate before starting calibration.")
    track = _find_track(manifest, track_id)
    input_window = _normalise_input_window(track, preview_start_seconds, preview_duration_seconds)
    job_id = f"calibration-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    estimated_duration = input_window["durationSeconds"] if input_window else float(track.get("durationSeconds", 0))
    tasks = [{
        "taskId": f"{job_id}:{candidate['slot']}",
        "kind": "calibration",
        "slot": candidate["slot"],
        "candidateId": candidate.get("candidateId"),
        "candidateLabel": candidate.get("label"),
        "trackId": track["trackId"],
        "stage": "Queued",
        "startedAt": None,
        "finishedAt": None,
        "elapsedSeconds": None,
        "outputId": None,
        "error": None,
        "technicalError": None,
        "estimatedSeconds": estimate_candidate_seconds(candidate, estimated_duration),
        "previewOnly": input_window is not None,
    } for candidate in candidates]
    state = {
        "jobId": job_id,
        "kind": "calibration",
        "status": "queued",
        "stage": "Queued",
        "message": "Calibration queued.",
        "trackId": track["trackId"],
        "trackTitle": track["title"],
        "trackCount": len(manifest.get("tracks", [])),
        "inputWindow": input_window,
        "previewOnly": input_window is not None,
        "startedAt": now,
        "finishedAt": None,
        "estimatedAlbumSeconds": sum(item["estimatedSeconds"] for item in tasks if item.get("estimatedSeconds") is not None) or None,
        "estimateSource": "Local HQ5 benchmark" if any(candidate.get("benchmark") for candidate in candidates) else None,
        "tasks": tasks,
        "events": [],
    }
    state_path = _state_path(path, job_id)
    _event(state, "Queued", f"Calibration queued for {track['title']}.")
    _write_state(state_path, state)
    return job_id, state_path, state


def create_album_job(
    manifest_path: str | Path,
    *,
    track_ids: set[str] | None = None,
    slots: set[str] | None = None,
    force: bool = False,
    kind: str = "album",
) -> tuple[str, Path, dict[str, Any]]:
    path = normalized_path(manifest_path)
    manifest = load_manifest(path)
    _ensure_sources_available(manifest)
    candidates = _selected_candidates(manifest)
    tracks = manifest.get("tracks")
    if not candidates:
        raise ProcessingError("Choose at least one Candidate before starting album processing.")
    if not isinstance(tracks, list) or not tracks:
        raise ProcessingError("Choose an Album Project with at least one detected Track first.")
    job_id = f"album-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    outputs = manifest.get("outputs", {})
    tasks: list[dict[str, Any]] = []
    selected_tracks = [track for track in tracks if track_ids is None or track.get("trackId") in track_ids]
    selected_candidates = [candidate for candidate in candidates if slots is None or candidate.get("slot") in slots]
    if not selected_tracks or not selected_candidates:
        raise ProcessingError("The requested reprocess scope is empty.")
    for track in selected_tracks:
        for candidate in selected_candidates:
            output_id = f"album:{track['trackId']}:{candidate['slot']}"
            existing = outputs.get(output_id) if isinstance(outputs, dict) else None
            complete = not force and output_cache_hit(existing, cache_identity(track, candidate, manifest))
            tasks.append({
                "taskId": f"{job_id}:{track['trackId']}:{candidate['slot']}",
                "kind": kind,
                "slot": candidate["slot"],
                "candidateId": candidate.get("candidateId"),
                "candidateLabel": candidate.get("label"),
                "trackId": track["trackId"],
                "stage": "Complete" if complete else "Queued",
                "startedAt": None,
                "finishedAt": existing.get("validatedAt") if complete and isinstance(existing, dict) else None,
                "elapsedSeconds": None,
                "outputId": output_id if complete else None,
                "error": None,
                "technicalError": None,
                "estimatedSeconds": estimate_candidate_seconds(candidate, float(track.get("durationSeconds", 0)), cold_start=not bool(candidate.get("reusableLoadedModel"))),
            })
    state = {
        "jobId": job_id,
        "kind": kind,
        "status": "queued",
        "stage": "Queued",
        "message": "Candidate Track queue ready." if kind == "candidate" else "Album queue ready.",
        "trackId": None,
        "trackTitle": None,
        "trackCount": len(selected_tracks),
        "totalTasks": len(tasks),
        "startedAt": now,
        "finishedAt": None,
        "estimatedAlbumSeconds": sum(item["estimatedSeconds"] for item in tasks if item.get("estimatedSeconds") is not None) or None,
        "estimateSource": "Local HQ5 benchmark" if any(candidate.get("benchmark") for candidate in selected_candidates) else None,
        "tasks": tasks,
        "events": [],
    }
    state_path = _state_path(path, job_id)
    _event(state, "Queued", f"Album queue ready: {len(tasks)} Track/Candidate tasks in Track-first order.")
    _write_state(state_path, state)
    return job_id, state_path, state


def _set_manifest_task(manifest_path: Path, task_id: str, task: dict[str, Any]) -> None:
    with project_mutation_lock(manifest_path.parent):
        manifest = load_manifest(manifest_path)
        tasks = manifest.setdefault("tasks", {})
        tasks[task_id] = task
        save_manifest(manifest, manifest_path.parent)


def _register_output(manifest_path: Path, output: dict[str, Any], task: dict[str, Any]) -> None:
    with project_mutation_lock(manifest_path.parent):
        manifest = load_manifest(manifest_path)
        outputs = manifest.setdefault("outputs", {})
        previous = outputs.get(output["outputId"])
        if previous and previous.get("path") and previous["path"] != output["path"]:
            old_path = Path(str(previous["path"]))
            if old_path.exists() and old_path.parent == Path(output["path"]).parent:
                old_path.unlink()
        outputs[output["outputId"]] = output
        manifest.setdefault("tasks", {})[task["taskId"]] = task
        save_manifest(manifest, manifest_path.parent)


def _probe_output(path: Path, source_duration: float, probe: Probe) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ProcessingError("The Candidate did not produce a non-empty Output.")
    metadata = probe(path)
    duration = float(metadata.get("durationSeconds", 0))
    tolerance = max(0.25, source_duration * 0.05)
    if duration <= 0 or abs(duration - source_duration) > tolerance:
        raise ProcessingError(f"Output duration {duration:.3f}s does not match source duration {source_duration:.3f}s.")
    return metadata


def _canonical_stem_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold().replace("-", "_")
    if normalized in {"instrumental", "other", "inst", "karaoke", "no_vocals"}:
        return "Instrumental"
    if normalized in {"vocal", "vocals"}:
        return "Vocals"
    return value.strip()


def _returned_path(separator: SeparatorContract, raw_path: str | Path) -> Path:
    path = Path(str(raw_path))
    if path.is_absolute():
        return path
    output_dir = getattr(separator, "output_dir", None)
    if output_dir:
        candidate = Path(str(output_dir)) / path
        if candidate.exists():
            return candidate
    return path


def _stem_name_from_returned_path(path: Path) -> str | None:
    match = re.search(r"_\(([^)]+)\)", path.name)
    return match.group(1) if match else None


def _capture_returned_outputs(
    separator: SeparatorContract,
    returned: list[str],
    preserve_dir: Path,
    *,
    slot: str,
    sequence: int,
    title: str,
) -> list[dict[str, Any]]:
    if not returned:
        raise ProcessingError("The Candidate returned no Output files.")
    preserve_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    explicit_names = getattr(separator, "returned_stem_names", {})
    for index, raw in enumerate(returned, start=1):
        engine_path = str(raw)
        source_path = _returned_path(separator, engine_path)
        if not source_path.is_file():
            raise ProcessingError(f"The Candidate returned a missing Output path: {engine_path}")
        name = None
        if isinstance(explicit_names, dict):
            name = explicit_names.get(engine_path) or explicit_names.get(source_path.name) or explicit_names.get(str(source_path))
        elif isinstance(explicit_names, list) and index <= len(explicit_names):
            name = explicit_names[index - 1]
        name = str(name) if name else _stem_name_from_returned_path(source_path)
        preserved = preserve_dir / f"{slot}_{sequence:02d}_{_slug(title)}_Returned_{index:02d}_{_slug(name or 'Unlabelled')}.wav"
        shutil.copyfile(source_path, preserved)
        records.append({
            "enginePath": engine_path,
            "engineName": source_path.name,
            "stemName": name,
            "preservedPath": str(preserved),
            "bytes": preserved.stat().st_size,
            "fileFingerprint": fingerprint_file(preserved),
        })
    return records


def _validate_returned_stems(
    separator: SeparatorContract,
    candidate: dict[str, Any],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    contract = getattr(separator, "stem_output_contract", None)
    if not isinstance(contract, dict):
        contract = candidate.get("stemOutputContract")
    target = _canonical_stem_name(str(contract.get("targetStem"))) if isinstance(contract, dict) and contract.get("targetStem") else _canonical_stem_name(str(candidate.get("targetStem") or ""))
    expected = contract.get("expectedStemNames") if isinstance(contract, dict) else None
    expected_canonical = {_canonical_stem_name(str(item)) for item in expected} if isinstance(expected, list) else set()
    if target:
        expected_canonical.add(target)
    if target == "Instrumental":
        expected_canonical.add("Vocals")
    if not target or not expected_canonical:
        raise ProcessingError("The Candidate did not expose an explicit semantic stem contract.")
    annotated: list[dict[str, Any]] = []
    for record in records:
        stem_name = record.get("stemName")
        canonical = _canonical_stem_name(str(stem_name)) if stem_name else None
        if canonical not in expected_canonical:
            raise ProcessingError("The Candidate returned an Output without a verified semantic stem name.")
        annotated_record = dict(record)
        annotated_record["stemName"] = stem_name
        annotated_record["canonicalStem"] = canonical
        annotated_record["role"] = "target" if canonical == target else "complementary"
        annotated.append(annotated_record)
    target_records = [record for record in annotated if record["role"] == "target"]
    complementary_records = [record for record in annotated if record["role"] == "complementary"]
    if len(target_records) != 1 or len(complementary_records) != 1:
        raise ProcessingError("The Candidate did not return exactly one target stem and one complementary stem.")
    return annotated


def _pick_instrumental(records: list[dict[str, Any]]) -> Path:
    candidates = [record for record in records if record.get("role") == "target" and record.get("canonicalStem") == "Instrumental"]
    if len(candidates) != 1:
        raise ProcessingError("The Candidate did not expose exactly one semantically verified Instrumental Output.")
    return Path(str(candidates[0]["preservedPath"]))


def _task_manifest_record(state_task: dict[str, Any], *, stage: str, error: str | None = None, output_id: str | None = None, returned_outputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "taskId": state_task["taskId"],
        "kind": state_task.get("kind", "calibration"),
        "slot": state_task["slot"],
        "candidateId": state_task.get("candidateId"),
        "trackId": state_task.get("trackId"),
        "stage": stage,
        "error": error,
        "outputId": output_id,
        "returnedOutputs": returned_outputs if returned_outputs is not None else state_task.get("returnedOutputs", []),
        "estimatedSeconds": state_task.get("estimatedSeconds"),
        "previewOnly": bool(state_task.get("previewOnly")),
        "semanticStatus": state_task.get("semanticStatus", "pending"),
        "updatedAt": utc_now(),
    }


def _run_one_task(
    manifest_file: Path,
    status_file: Path,
    model_cache: Path,
    state_task: dict[str, Any],
    candidate: dict[str, Any],
    track: dict[str, Any],
    *,
    output_namespace: str,
    separator_factory: SeparatorFactory,
    stop_event: EventType | None,
    probe: Probe,
    separator_cache: dict[str, SeparatorContract] | None = None,
    input_window: dict[str, Any] | None = None,
) -> tuple[str, float, bool]:
    task_id = state_task["taskId"]
    slot = state_task["slot"]
    started = time.perf_counter()
    started_at = utc_now()
    source_duration = float(track.get("durationSeconds", 0))
    _update_task(status_file, task_id, stage="Downloading model", started_at=started_at)
    _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Downloading model"))
    _update_state(status_file, stage="Downloading model", message=f"{track['title']} · Candidate {slot}: preparing {candidate.get('label', candidate.get('candidateId'))}.", task_id=task_id, slot=slot)
    task_dir = manifest_file.parent / ".stem-comparison" / "tmp" / status_file.parent.name / slot / _slug(str(track["trackId"]))
    task_dir.mkdir(parents=True, exist_ok=True)
    output_id = f"{output_namespace}:{track['trackId']}:{slot}"
    final_dir = manifest_file.parent / "outputs" / output_namespace
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{slot}_{int(track['sequence']):02d}_{_slug(track['title'])}_Instrumental.wav"
    returned_outputs: list[dict[str, Any]] = []
    try:
        source_path, processed_duration = _prepare_preview_input(Path(str(track["sourcePath"])), task_dir, input_window, probe)
        separator = _task_separator(separator_factory, candidate, model_cache, task_dir, separator_cache)
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Stop requested")
        _update_task(status_file, task_id, stage="Processing")
        _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Processing"))
        _update_state(status_file, stage="Processing", message=f"{track['title']} · Candidate {slot}: separating on CPU.", task_id=task_id, slot=slot)
        returned = separator.separate(str(source_path), None)
        returned_outputs = _capture_returned_outputs(
            separator,
            [str(item) for item in returned],
            final_dir / "returned",
            slot=slot,
            sequence=int(track["sequence"]),
            title=track["title"],
        )
        state_task["returnedOutputs"] = returned_outputs
        _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Processing", returned_outputs=returned_outputs))
        returned_outputs = _validate_returned_stems(separator, candidate, returned_outputs)
        state_task["returnedOutputs"] = returned_outputs
        selected = _pick_instrumental(returned_outputs)
        _update_task(status_file, task_id, stage="Validating")
        _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Validating"))
        _update_state(status_file, stage="Validating", message=f"{track['title']} · Candidate {slot}: validating WAV with FFprobe.", task_id=task_id, slot=slot)
        metadata = _probe_output(selected, processed_duration, probe)
        shutil.copyfile(selected, final_path)
        selected_record = next(record for record in returned_outputs if record.get("role") == "target")
        elapsed = time.perf_counter() - started
        output = {
            "outputId": output_id,
            "taskId": task_id,
            "trackId": track["trackId"],
            "slot": slot,
            "candidateId": candidate.get("candidateId"),
            "candidate": dict(candidate),
            "stem": "Instrumental",
            "path": str(final_path),
            "format": "WAV",
            "durationSeconds": metadata["durationSeconds"],
            "sourceDurationSeconds": source_duration,
            "processedDurationSeconds": processed_duration,
            "isPreview": input_window is not None,
            "previewWindow": input_window,
            "codec": metadata.get("codec"),
            "cacheIdentity": cache_identity(track, candidate, load_manifest(manifest_file)),
            "fileFingerprint": fingerprint_file(final_path),
            "validatedAt": utc_now(),
            "status": "valid",
            "semanticStatus": "pending",
            "engineStemName": selected_record.get("stemName"),
            "semanticValidation": {
                "role": "target",
                "canonicalStem": "Instrumental",
                "returnedOutputs": returned_outputs,
                "contract": getattr(separator, "stem_output_contract", None) or candidate.get("stemOutputContract"),
            },
            "provenance": {"returnedOutputs": returned_outputs, "inputWindow": input_window, "semanticStatus": "pending"},
        }
        state_task.update({"stage": "Complete", "finishedAt": utc_now(), "elapsedSeconds": round(elapsed, 3), "outputId": output_id, "error": None})
        _register_output(manifest_file, output, _task_manifest_record(state_task, stage="Complete", output_id=output_id, returned_outputs=returned_outputs))
        _update_task(status_file, task_id, stage="Complete", finished_at=state_task["finishedAt"], elapsed_seconds=elapsed, output_id=output_id)
        _update_state(status_file, stage="Complete", message=f"{track['title']} · Candidate {slot}: valid Instrumental Output ready.", task_id=task_id, slot=slot)
        return "Complete", elapsed, False
    except InterruptedError:
        elapsed = time.perf_counter() - started
        _update_task(status_file, task_id, stage="Skipped", finished_at=utc_now(), elapsed_seconds=elapsed)
        _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Skipped"))
        _update_state(status_file, stage="Skipped", message=f"{track['title']} · Candidate {slot} skipped after stop request.", task_id=task_id, slot=slot)
        return "Skipped", elapsed, True
    except Exception as exc:
        elapsed = time.perf_counter() - started
        error = str(exc) or exc.__class__.__name__
        _update_task(status_file, task_id, stage="Failed", finished_at=utc_now(), elapsed_seconds=elapsed, error=error, technical_error=traceback.format_exc())
        _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Failed", error=error))
        _update_state(status_file, stage="Failed", message=f"{track['title']} · Candidate {slot} failed: {error}", task_id=task_id, slot=slot)
        return "Failed", elapsed, False
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def run_calibration_job(
    manifest_path: str | Path,
    state_path: str | Path,
    model_cache: str | Path,
    *,
    separator_factory: SeparatorFactory | None = None,
    stop_event: EventType | None = None,
    probe: Probe = _probe_with_ffprobe,
) -> None:
    manifest_file = normalized_path(manifest_path)
    status_file = normalized_path(state_path)
    cache = normalized_path(model_cache)
    factory = separator_factory or _production_separator_factory
    state = _read_state(status_file)
    manifest = load_manifest(manifest_file)
    track = _find_track(manifest, state.get("trackId"))
    source_duration = float(track.get("durationSeconds", 0))
    input_window = state.get("inputWindow") if isinstance(state.get("inputWindow"), dict) else None
    completed_elapsed: list[float] = []
    failures = 0
    stopped = False
    separator_cache: dict[str, SeparatorContract] = {}
    state["status"] = "running"
    state["startedAt"] = state.get("startedAt") or utc_now()
    _event(state, "Running", f"Calibration started for {track['title']}.")
    _write_state(status_file, state)

    for state_task in state.get("tasks", []):
        task_id = state_task["taskId"]
        slot = state_task["slot"]
        candidate = next((item for item in _selected_candidates(manifest) if item.get("slot") == slot), None)
        if candidate is None:
            failures += 1
            _update_task(status_file, task_id, stage="Failed", error="Candidate is no longer present in the Album Project.", finished_at=utc_now())
            _update_state(status_file, stage="Failed", message="Candidate is no longer present in the Album Project.", task_id=task_id, slot=slot)
            continue
        if stop_event is not None and stop_event.is_set():
            stopped = True
            _update_task(status_file, task_id, stage="Skipped", finished_at=utc_now())
            _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Skipped"))
            _update_state(status_file, stage="Skipped", message=f"Candidate {slot} skipped after stop request.", task_id=task_id, slot=slot)
            continue
        started = time.perf_counter()
        started_at = utc_now()
        _update_task(status_file, task_id, stage="Downloading model", started_at=started_at)
        _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Downloading model"))
        _update_state(status_file, stage="Downloading model", message=f"Candidate {slot}: preparing {candidate.get('label', candidate.get('candidateId'))}.", task_id=task_id, slot=slot)
        task_dir = manifest_file.parent / ".stem-comparison" / "tmp" / state["jobId"] / slot
        task_dir.mkdir(parents=True, exist_ok=True)
        output_id = f"calibration:{track['trackId']}:{slot}"
        final_dir = manifest_file.parent / "outputs" / "calibration"
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / f"{slot}_{int(track['sequence']):02d}_{_slug(track['title'])}_Instrumental.wav"
        returned_outputs: list[dict[str, Any]] = []
        try:
            source_path, processed_duration = _prepare_preview_input(Path(str(track["sourcePath"])), task_dir, input_window, probe)
            separator = _task_separator(factory, candidate, cache, task_dir, None)
            if stop_event is not None and stop_event.is_set():
                stopped = True
                raise InterruptedError("Stop requested")
            _update_task(status_file, task_id, stage="Processing")
            _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Processing"))
            _update_state(status_file, stage="Processing", message=f"Candidate {slot}: separating on CPU.", task_id=task_id, slot=slot)
            returned = separator.separate(str(source_path), None)
            returned_outputs = _capture_returned_outputs(
                separator,
                [str(item) for item in returned],
                final_dir / "returned",
                slot=slot,
                sequence=int(track["sequence"]),
                title=track["title"],
            )
            state_task["returnedOutputs"] = returned_outputs
            _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Processing", returned_outputs=returned_outputs))
            returned_outputs = _validate_returned_stems(separator, candidate, returned_outputs)
            state_task["returnedOutputs"] = returned_outputs
            selected = _pick_instrumental(returned_outputs)
            _update_task(status_file, task_id, stage="Validating")
            _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Validating"))
            _update_state(status_file, stage="Validating", message=f"Candidate {slot}: validating WAV with FFprobe.", task_id=task_id, slot=slot)
            metadata = _probe_output(selected, processed_duration, probe)
            shutil.copyfile(selected, final_path)
            selected_record = next(record for record in returned_outputs if record.get("role") == "target")
            elapsed = time.perf_counter() - started
            output = {
                "outputId": output_id,
                "taskId": task_id,
                "trackId": track["trackId"],
                "slot": slot,
                "candidateId": candidate.get("candidateId"),
                "candidate": dict(candidate),
                "stem": "Instrumental",
                "path": str(final_path),
                "format": "WAV",
                "durationSeconds": metadata["durationSeconds"],
                "sourceDurationSeconds": source_duration,
                "processedDurationSeconds": processed_duration,
                "isPreview": input_window is not None,
                "previewWindow": input_window,
                "codec": metadata.get("codec"),
                "cacheIdentity": cache_identity(track, candidate, load_manifest(manifest_file)),
                "fileFingerprint": fingerprint_file(final_path),
                "validatedAt": utc_now(),
                "status": "valid",
                "semanticStatus": "pending",
                "engineStemName": selected_record.get("stemName"),
                "semanticValidation": {
                    "role": "target",
                    "canonicalStem": "Instrumental",
                    "returnedOutputs": returned_outputs,
                    "contract": getattr(separator, "stem_output_contract", None) or candidate.get("stemOutputContract"),
                },
                "provenance": {"returnedOutputs": returned_outputs, "inputWindow": input_window, "semanticStatus": "pending"},
            }
            state_task.update({"stage": "Complete", "finishedAt": utc_now(), "elapsedSeconds": round(elapsed, 3), "outputId": output_id, "error": None})
            _register_output(manifest_file, output, _task_manifest_record(state_task, stage="Complete", output_id=output_id, returned_outputs=returned_outputs))
            completed_elapsed.append(elapsed)
            _update_task(status_file, task_id, stage="Complete", finished_at=state_task["finishedAt"], elapsed_seconds=elapsed, output_id=output_id)
            estimate = (sum(completed_elapsed) / len(completed_elapsed)) * int(state.get("trackCount", 1))
            _update_state(status_file, stage="Complete", message=f"Candidate {slot}: valid Instrumental Output ready.", task_id=task_id, slot=slot, estimatedAlbumSeconds=round(estimate, 1))
        except InterruptedError:
            stopped = True
            _update_task(status_file, task_id, stage="Skipped", finished_at=utc_now(), elapsed_seconds=time.perf_counter() - started)
            _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Skipped"))
            _update_state(status_file, stage="Skipped", message=f"Candidate {slot} skipped after stop request.", task_id=task_id, slot=slot)
        except Exception as exc:
            failures += 1
            elapsed = time.perf_counter() - started
            error = str(exc) or exc.__class__.__name__
            _update_task(status_file, task_id, stage="Failed", finished_at=utc_now(), elapsed_seconds=elapsed, error=error, technical_error=traceback.format_exc())
            _set_manifest_task(manifest_file, task_id, _task_manifest_record(state_task, stage="Failed", error=error))
            _update_state(status_file, stage="Failed", message=f"Candidate {slot} failed: {error}", task_id=task_id, slot=slot)
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
        manifest = load_manifest(manifest_file)

    final_status = "stopped" if stopped else "complete" if completed_elapsed else "failed"
    message = "Calibration stopped." if stopped else "Calibration complete." if not failures else "Calibration complete with failed Candidates."
    _update_state(status_file, status=final_status, stage="Complete" if final_status == "complete" else final_status.title(), message=message, finishedAt=utc_now(), hasFailures=bool(failures))


def run_album_job(
    manifest_path: str | Path,
    state_path: str | Path,
    model_cache: str | Path,
    *,
    separator_factory: SeparatorFactory | None = None,
    stop_event: EventType | None = None,
    probe: Probe = _probe_with_ffprobe,
) -> None:
    manifest_file = normalized_path(manifest_path)
    status_file = normalized_path(state_path)
    cache = normalized_path(model_cache)
    factory = separator_factory or _production_separator_factory
    state = _read_state(status_file)
    manifest = load_manifest(manifest_file)
    tracks = {track["trackId"]: track for track in manifest.get("tracks", [])}
    candidates = {candidate["slot"]: candidate for candidate in _selected_candidates(manifest)}
    state["status"] = "running"
    state["startedAt"] = state.get("startedAt") or utc_now()
    _event(state, "Running", "Album processing started in Track-first order.")
    _write_state(status_file, state)
    completed_elapsed: list[float] = []
    failures = 0
    stopped = False
    separator_cache: dict[str, SeparatorContract] = {}
    for state_task in state.get("tasks", []):
        if state_task.get("stage") == "Complete" and state_task.get("outputId"):
            continue
        if stop_event is not None and stop_event.is_set():
            stopped = True
            _update_task(status_file, state_task["taskId"], stage="Skipped", finished_at=utc_now())
            _set_manifest_task(manifest_file, state_task["taskId"], _task_manifest_record(state_task, stage="Skipped"))
            _update_state(status_file, stage="Skipped", message=f"{state_task['trackId']} · Candidate {state_task['slot']} skipped after stop request.", task_id=state_task["taskId"], slot=state_task["slot"])
            continue
        track = tracks.get(state_task.get("trackId"))
        candidate = candidates.get(state_task.get("slot"))
        if track is None or candidate is None:
            failures += 1
            _update_task(status_file, state_task["taskId"], stage="Failed", finished_at=utc_now(), error="Track or Candidate is no longer present in the Album Project.")
            _set_manifest_task(manifest_file, state_task["taskId"], _task_manifest_record(state_task, stage="Failed", error="Track or Candidate is no longer present in the Album Project."))
            continue
        state["trackId"] = track["trackId"]
        state["trackTitle"] = track["title"]
        state["stage"] = "Queued"
        _write_state(status_file, state)
        stage, elapsed, task_stopped = _run_one_task(manifest_file, status_file, cache, state_task, candidate, track, output_namespace="album", separator_factory=factory, stop_event=stop_event, probe=probe, separator_cache=separator_cache)
        if stage == "Complete":
            completed_elapsed.append(elapsed)
            state = _read_state(status_file)
            remaining = sum(1 for task in state.get("tasks", []) if task.get("stage") in {"Queued", "Downloading model", "Processing", "Validating"})
            rolling_eta = (sum(completed_elapsed) / len(completed_elapsed)) * remaining if completed_elapsed else None
            _update_state(status_file, estimatedAlbumSeconds=round(rolling_eta, 1) if rolling_eta is not None else None, completedOutputs=len(completed_elapsed))
        elif stage == "Failed":
            failures += 1
        if task_stopped:
            stopped = True
        if stopped:
            continue
    final_status = "stopped" if stopped else "complete" if completed_elapsed or all(task.get("stage") == "Complete" for task in _read_state(status_file).get("tasks", [])) else "failed"
    message = "Album processing stopped." if stopped else "Album processing complete." if not failures else "Album processing complete with failed tasks."
    _update_state(status_file, status=final_status, stage="Complete" if final_status == "complete" else final_status.title(), message=message, finishedAt=utc_now(), hasFailures=bool(failures), completedOutputs=len(completed_elapsed))


def latest_calibration_state(manifest_path: str | Path) -> dict[str, Any] | None:
    root = _job_root(normalized_path(manifest_path))
    states = [path for path in root.glob("*/status.json") if path.is_file()]
    if not states:
        return None
    latest = max(states, key=lambda path: path.stat().st_mtime_ns)
    return read_json(latest, None)


def reconcile_jobs(manifest_path: str | Path, active_job_ids: set[str] | None = None) -> list[str]:
    manifest_file = normalized_path(manifest_path)
    active = active_job_ids or set()
    root = _job_root(manifest_file)
    changed: list[str] = []
    manifest = load_manifest(manifest_file)
    tracks = {track.get("trackId"): track for track in manifest.get("tracks", [])}
    candidates = {candidate.get("slot"): candidate for candidate in _selected_candidates(manifest)}
    for state_file in root.glob("*/status.json"):
        state = read_json(state_file, None)
        if not isinstance(state, dict) or state.get("jobId") in active or state.get("status") not in {"queued", "running"}:
            continue
        for task in state.get("tasks", []):
            if task.get("stage") != "Complete":
                task["stage"] = "Queued"
                task["startedAt"] = None
                task["finishedAt"] = None
                task["elapsedSeconds"] = None
                task["error"] = None
                task["technicalError"] = None
                task["outputId"] = None
                continue
            track = tracks.get(task.get("trackId"))
            candidate = candidates.get(task.get("slot"))
            output = manifest.get("outputs", {}).get(task.get("outputId")) if isinstance(manifest.get("outputs"), dict) else None
            if not track or not candidate or not output_cache_hit(output, cache_identity(track, candidate, manifest)):
                task["stage"] = "Queued"
                task["finishedAt"] = None
                task["elapsedSeconds"] = None
                task["outputId"] = None
        state["status"] = "failed"
        state["stage"] = "Interrupted"
        state["message"] = "Worker or backend stopped; valid Outputs were retained and incomplete tasks are queued for resume."
        state["finishedAt"] = utc_now()
        _event(state, "Interrupted", state["message"])
        _write_state(state_file, state)
        changed.append(str(state.get("jobId")))
        shutil.rmtree(manifest_file.parent / ".stem-comparison" / "tmp" / str(state.get("jobId")), ignore_errors=True)
    return changed


def cleanup_temporary(manifest_path: str | Path) -> dict[str, Any]:
    root = normalized_path(manifest_path).parent / ".stem-comparison" / "tmp"
    removed = 0
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
                removed += 1
            else:
                child.unlink()
                removed += 1
    return {"removed": removed, "scope": str(root)}


def stop_calibration_state(state_path: str | Path) -> dict[str, Any]:
    path = normalized_path(state_path)
    state = _read_state(path)
    if state.get("status") not in TERMINAL_JOB_STATUSES:
        state["stopRequested"] = True
        _event(state, "Stop requested", "Stop requested; the current Candidate will finish its safe boundary.")
        _write_state(path, state)
    return state


def skip_calibration(manifest_path: str | Path) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        manifest.setdefault("settings", {})["calibrationSkipped"] = True
        manifest.setdefault("tasks", {})["calibration:skip"] = {
            "taskId": "calibration:skip",
            "kind": "calibration",
            "stage": "Skipped",
            "reason": "User acknowledged calibration uncertainty",
            "updatedAt": utc_now(),
        }
        save_manifest(manifest, path.parent)
    return manifest
