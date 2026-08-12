from __future__ import annotations

from pathlib import Path
from typing import Any

from .processing import fingerprint_file
from .json_store import read_json
from .projects import ProjectError, load_manifest, normalized_path, project_mutation_lock, save_manifest, utc_now


def _track(manifest: dict[str, Any], track_id: str) -> dict[str, Any]:
    track = next((item for item in manifest.get("tracks", []) if item.get("trackId") == track_id), None)
    if not isinstance(track, dict):
        raise ProjectError("The selected Track is not in the Album Project.")
    return track


def update_loop(manifest_path: str, track_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        track = _track(manifest, track_id)
        in_seconds = payload.get("inSeconds")
        out_seconds = payload.get("outSeconds")
        enabled = bool(payload.get("enabled", False))
        try:
            in_value = max(0.0, float(in_seconds)) if in_seconds is not None else None
            out_value = max(0.0, float(out_seconds)) if out_seconds is not None else None
        except (TypeError, ValueError) as exc:
            raise ProjectError("Loop boundaries must be numeric seconds.") from exc
        duration = float(track.get("durationSeconds", 0))
        if in_value is not None and in_value > duration:
            raise ProjectError("Loop In must be inside the Track duration.")
        if out_value is not None and out_value > duration:
            raise ProjectError("Loop Out must be inside the Track duration.")
        if in_value is not None and out_value is not None and out_value <= in_value:
            raise ProjectError("Loop Out must be later than Loop In.")
        loops = manifest.setdefault("loops", {})
        if in_value is None and out_value is None and not enabled:
            loops.pop(track_id, None)
        else:
            loops[track_id] = {"trackId": track_id, "inSeconds": in_value, "outSeconds": out_value, "enabled": enabled, "updatedAt": utc_now()}
        save_manifest(manifest, path.parent)
    return manifest


def _output_for_selection(manifest: dict[str, Any], track_id: str, slot: str, output_id: str | None) -> dict[str, Any]:
    outputs = manifest.get("outputs", {})
    if output_id:
        output = outputs.get(output_id) if isinstance(outputs, dict) else None
    else:
        candidates = [item for item in outputs.values() if isinstance(item, dict) and item.get("trackId") == track_id and item.get("slot") == slot and item.get("status") == "valid"] if isinstance(outputs, dict) else []
        candidates.sort(key=lambda item: (0 if str(item.get("outputId", "")).startswith("album:") else 1, str(item.get("validatedAt", ""))))
        output = candidates[0] if candidates else None
    if not isinstance(output, dict) or output.get("trackId") != track_id or output.get("slot") != slot or output.get("status") != "valid":
        raise ProjectError("Choose a registered valid Output for this Track and Candidate.")
    if output.get("isPreview"):
        raise ProjectError("This is a calibration preview only; process the full Track before Selection.")
    if output.get("semanticStatus") != "confirmed":
        raise ProjectError("Listen to the Output and save semantic confirmation of its Instrumental identity before Selection.")
    media_path = normalized_path(str(output.get("path", "")))
    if not media_path.is_file():
        raise ProjectError("This Output is no longer available; choose another Candidate.")
    fingerprint = output.get("fileFingerprint")
    if not fingerprint:
        raise ProjectError("This Output has no registered integrity fingerprint; choose another Candidate.")
    if fingerprint_file(media_path) != fingerprint:
        raise ProjectError("This Output failed integrity validation; choose another Candidate.")
    return output


def _confirmation_candidate(manifest: dict[str, Any], output_id: str) -> dict[str, Any]:
    output = manifest.get("outputs", {}).get(output_id) if isinstance(manifest.get("outputs"), dict) else None
    if not isinstance(output, dict) or output.get("status") != "valid":
        raise ProjectError("Only a registered valid Output can receive semantic confirmation.")
    if output.get("isPreview"):
        raise ProjectError("Calibration previews cannot receive semantic confirmation.")
    if not output.get("semanticValidation"):
        raise ProjectError("This Output has no recorded semantic stem contract.")
    media_path = normalized_path(str(output.get("path", "")))
    if not media_path.is_file():
        raise ProjectError("This Output is no longer available; semantic confirmation is blocked.")
    fingerprint = output.get("fileFingerprint")
    if not fingerprint or fingerprint_file(media_path) != fingerprint:
        raise ProjectError("This Output failed integrity validation; semantic confirmation is blocked.")
    return output


def _confirm_output_locked(manifest: dict[str, Any], output_id: str, manifest_root: Path) -> dict[str, Any]:
    output = _confirmation_candidate(manifest, output_id)
    confirmed_at = str(output.get("semanticConfirmedAt") or utc_now())
    output["semanticStatus"] = "confirmed"
    output["semanticConfirmedAt"] = confirmed_at
    provenance = output.setdefault("provenance", {})
    provenance["semanticStatus"] = "confirmed"
    provenance["semanticConfirmedAt"] = confirmed_at
    for task in manifest.setdefault("tasks", {}).values():
        if isinstance(task, dict) and task.get("outputId") == output_id:
            task["semanticStatus"] = "confirmed"
            task["semanticConfirmedAt"] = confirmed_at
            task_id = str(task.get("taskId", ""))
            job_id = task_id.split(":", 1)[0]
            job_state = read_json(manifest_root / ".stem-comparison" / "jobs" / job_id / "status.json", None)
            if isinstance(job_state, dict) and job_state.get("kind"):
                task["kind"] = job_state["kind"]
    confirmations = manifest.setdefault("semanticConfirmations", [])
    if not any(isinstance(item, dict) and item.get("outputId") == output_id for item in confirmations):
        confirmations.append({
            "outputId": output_id,
            "trackId": output.get("trackId"),
            "slot": output.get("slot"),
            "confirmedAt": confirmed_at,
            "isPreview": bool(output.get("isPreview")),
        })
    return output


def confirm_output_semantics(manifest_path: str, output_id: str) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        _confirm_output_locked(manifest, output_id, path.parent)
        save_manifest(manifest, path.parent)
    return manifest


def _selection_summary(manifest: dict[str, Any]) -> str:
    selections = manifest.setdefault("selections", {})
    return "\n".join(
        f"{int(item.get('sequence', 0)):02d} — {item.get('title')}: Candidate {selections.get(item.get('trackId'), {}).get('slot', 'Not selected')}"
        for item in manifest.get("tracks", [])
    )


def _select_candidate_locked(manifest: dict[str, Any], track_id: str, slot: str, output_id: str | None = None) -> dict[str, Any]:
    track = _track(manifest, track_id)
    output = _output_for_selection(manifest, track_id, slot, output_id)
    selections = manifest.setdefault("selections", {})
    previous = selections.get(track_id)
    if isinstance(previous, dict) and previous.get("outputId") == output["outputId"] and previous.get("slot") == slot:
        return output
    if previous:
        manifest.setdefault("selectionHistory", []).append(previous)
    selections[track_id] = {
        "trackId": track_id,
        "trackTitle": track["title"],
        "slot": slot,
        "candidateId": output.get("candidateId"),
        "candidate": output.get("candidate"),
        "outputId": output["outputId"],
        "outputFingerprint": output.get("fileFingerprint"),
        "selectedAt": utc_now(),
    }
    manifest["selectionSummary"] = _selection_summary(manifest)
    return output


def select_candidate(manifest_path: str, track_id: str, slot: str, output_id: str | None = None) -> dict[str, Any]:
    if slot not in {"A", "B", "C", "D"}:
        raise ProjectError("Selection slot must be A, B, C, or D.")
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        _select_candidate_locked(manifest, track_id, slot, output_id)
        save_manifest(manifest, path.parent)
    return manifest


def approve_and_select(manifest_path: str, track_id: str, slot: str, output_id: str | None = None) -> dict[str, Any]:
    if slot not in {"A", "B", "C", "D"}:
        raise ProjectError("Selection slot must be A, B, C, or D.")
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        output = _confirmation_candidate(manifest, output_id) if output_id else next(
            (item for item in manifest.get("outputs", {}).values() if isinstance(item, dict) and item.get("trackId") == track_id and item.get("slot") == slot and item.get("status") == "valid"),
            None,
        )
        if not isinstance(output, dict):
            raise ProjectError("Choose a registered valid Output for this Track and Candidate.")
        output_id = str(output["outputId"])
        _confirm_output_locked(manifest, output_id, path.parent)
        _select_candidate_locked(manifest, track_id, slot, output_id)
        save_manifest(manifest, path.parent)
    return manifest


def _single_candidate(manifest: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    candidates = [item for item in manifest.get("candidates", []) if isinstance(item, dict) and item.get("candidateId")]
    if len(candidates) != 1:
        raise ProjectError("Approve and select all is available only for a single-Candidate Album Project.")
    candidate = candidates[0]
    slot = str(candidate.get("slot") or candidate.get("defaultSlot") or "A")
    if slot not in {"A", "B", "C", "D"}:
        raise ProjectError("The single Candidate has no valid slot.")
    return slot, candidate


def approve_and_select_all(manifest_path: str) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        slot, candidate = _single_candidate(manifest)
        results: list[dict[str, Any]] = []
        approved = 0
        pending = 0
        outputs = manifest.get("outputs", {})
        for track in manifest.get("tracks", []):
            matches = [
                item for item in outputs.values()
                if isinstance(item, dict) and item.get("trackId") == track.get("trackId") and item.get("slot") == slot and item.get("candidateId") == candidate.get("candidateId")
            ] if isinstance(outputs, dict) else []
            matches.sort(key=lambda item: (0 if str(item.get("outputId", "")).startswith("album:") else 1, str(item.get("validatedAt", ""))))
            output = matches[0] if matches else None
            if output is None:
                results.append({"trackId": track.get("trackId"), "trackTitle": track.get("title"), "status": "skipped", "reason": "No Output for the single Candidate."})
                continue
            if output.get("semanticStatus") == "confirmed":
                results.append({"trackId": track.get("trackId"), "trackTitle": track.get("title"), "outputId": output.get("outputId"), "status": "skipped", "reason": "Output is already semantically confirmed."})
                continue
            try:
                _confirmation_candidate(manifest, str(output["outputId"]))
            except ProjectError as exc:
                results.append({"trackId": track.get("trackId"), "trackTitle": track.get("title"), "outputId": output.get("outputId"), "status": "skipped", "reason": str(exc)})
                continue
            pending += 1
            try:
                _confirm_output_locked(manifest, str(output["outputId"]), path.parent)
                _select_candidate_locked(manifest, str(track["trackId"]), slot, str(output["outputId"]))
            except ProjectError as exc:
                results.append({"trackId": track.get("trackId"), "trackTitle": track.get("title"), "outputId": output.get("outputId"), "status": "skipped", "reason": str(exc)})
                continue
            approved += 1
            results.append({"trackId": track.get("trackId"), "trackTitle": track.get("title"), "outputId": output.get("outputId"), "status": "approved-selected", "slot": slot})
        manifest["selectionSummary"] = _selection_summary(manifest)
        if approved:
            save_manifest(manifest, path.parent)
    return {"project": manifest, "candidateSlot": slot, "candidateId": candidate.get("candidateId"), "candidateLabel": candidate.get("label"), "pending": pending, "approved": approved, "results": results}


def invalidate_output(manifest_path: str, output_id: str, reason: str = "Output rejected during human review.") -> dict[str, Any]:
    path = normalized_path(manifest_path)
    with project_mutation_lock(path.parent):
        manifest = load_manifest(path)
        output = manifest.get("outputs", {}).get(output_id) if isinstance(manifest.get("outputs"), dict) else None
        if not isinstance(output, dict):
            raise ProjectError("Only a registered Output can be rejected.")
        if output.get("status") == "invalid":
            return manifest
        invalidated_at = utc_now()
        output["status"] = "invalid"
        output["semanticStatus"] = "rejected"
        output["invalidReason"] = reason
        output["invalidatedAt"] = invalidated_at
        output["provenance"] = {**(output.get("provenance") if isinstance(output.get("provenance"), dict) else {}), "productValidity": "invalid", "invalidReason": reason, "invalidatedAt": invalidated_at}
        selections = manifest.setdefault("selections", {})
        for track_id, selection in list(selections.items()):
            if isinstance(selection, dict) and selection.get("outputId") == output_id:
                manifest.setdefault("selectionHistory", []).append({**selection, "invalidatedAt": invalidated_at, "invalidReason": reason})
                selections.pop(track_id, None)
        manifest["selectionSummary"] = _selection_summary(manifest)
        manifest["export"] = {"status": "invalidated", "reason": reason, "items": {}, "updatedAt": invalidated_at}
        save_manifest(manifest, path.parent)
    return manifest
