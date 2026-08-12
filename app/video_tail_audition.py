from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .json_store import atomic_write_json, read_json
from .projects import ProjectError, normalized_path, utc_now
from .video import build_video_state


TAIL_AUDITION_SCHEMA_VERSION = 1
TAIL_LOOKBACK_SECONDS = 8.0
NEXT_TRACK_PREVIEW_SECONDS = 4.0
TAIL_DECISION_PATH = Path("video") / "tail-auditions.json"
VALID_DECISIONS = {"pending", "keep-current", "use-proposed"}


class VideoTailAuditionError(ProjectError):
    pass


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _decision_file(root: Path) -> Path:
    return root / TAIL_DECISION_PATH


def _read_decisions(root: Path) -> dict[str, Any]:
    value = read_json(_decision_file(root), {})
    return value if isinstance(value, dict) else {}


def _review_card(track: dict[str, Any], next_track: dict[str, Any] | None) -> dict[str, Any] | None:
    current_duration = float(track.get("durationSeconds", 0) or 0)
    removed = max(0.0, float(track.get("proposedRemovalSeconds", 0) or 0))
    if current_duration <= 0 or removed <= 0:
        return None
    proposed_duration = max(0.0, current_duration - removed)
    start = max(0.0, proposed_duration - TAIL_LOOKBACK_SECONDS)
    fingerprint = _digest({
        "trackId": track.get("trackId"),
        "fileFingerprint": track.get("fileFingerprint"),
        "currentDurationSeconds": round(current_duration, 6),
        "proposedRemovalSeconds": round(removed, 6),
        "nextTrackId": next_track.get("trackId") if next_track else None,
        "nextFileFingerprint": next_track.get("fileFingerprint") if next_track else None,
    })
    return {
        "trackId": str(track.get("trackId")),
        "sequence": int(track.get("sequence", 0)),
        "title": str(track.get("title", "")),
        "currentSourceUrl": f"/api/video/audio/{track.get('trackId')}",
        "nextSourceUrl": f"/api/video/audio/{next_track.get('trackId')}" if next_track else None,
        "startSeconds": round(start, 6),
        "currentEndSeconds": round(current_duration, 6),
        "proposedEndSeconds": round(proposed_duration, 6),
        "nextPreviewSeconds": round(min(NEXT_TRACK_PREVIEW_SECONDS, float(next_track.get("durationSeconds", 0) or 0)) if next_track else 0.0, 6),
        "currentDurationSeconds": round(current_duration, 6),
        "proposedDurationSeconds": round(proposed_duration, 6),
        "removedSeconds": round(removed, 6),
        "inputFingerprint": fingerprint,
        "decision": "pending",
        "decisionUpdatedAt": None,
    }


def build_tail_audition_state(project_manifest: str | Path) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    state = build_video_state(manifest_path, _skip_prepare=True)
    if not state.get("ready"):
        raise VideoTailAuditionError("Tail audition is blocked until the current Final Instrumentals and Video configuration are valid.")
    tracks = sorted(state["composition"].get("timeline", []), key=lambda item: int(item.get("sequence", 0)))
    stored_value = _read_decisions(root).get("decisions")
    stored = stored_value if isinstance(stored_value, dict) else {}
    cards: list[dict[str, Any]] = []
    for index, track in enumerate(tracks):
        card = _review_card(track, tracks[index + 1] if index + 1 < len(tracks) else None)
        if not card:
            continue
        saved = stored.get(card["trackId"]) if isinstance(stored, dict) else None
        if isinstance(saved, dict) and saved.get("inputFingerprint") == card["inputFingerprint"] and saved.get("decision") in VALID_DECISIONS:
            card["decision"] = saved["decision"]
            card["decisionUpdatedAt"] = saved.get("updatedAt")
        cards.append(card)
    return {
        "schemaVersion": TAIL_AUDITION_SCHEMA_VERSION,
        "projectFolder": str(root),
        "source": "current validated Final Instrumentals only",
        "lookbackSeconds": TAIL_LOOKBACK_SECONDS,
        "nextTrackPreviewSeconds": NEXT_TRACK_PREVIEW_SECONDS,
        "cards": cards,
    }


def save_tail_audition_decision(project_manifest: str | Path, track_id: str, decision: str) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise VideoTailAuditionError("Tail decision must be pending, keep-current, or use-proposed.")
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    state = build_tail_audition_state(manifest_path)
    card = next((item for item in state["cards"] if item["trackId"] == track_id), None)
    if not card:
        raise VideoTailAuditionError("The requested Track has no pending tail audition.")
    existing = _read_decisions(root)
    decisions = existing.get("decisions") if isinstance(existing.get("decisions"), dict) else {}
    decisions[track_id] = {"decision": decision, "inputFingerprint": card["inputFingerprint"], "updatedAt": utc_now()}
    atomic_write_json(_decision_file(root), {"schemaVersion": TAIL_AUDITION_SCHEMA_VERSION, "decisions": decisions})
    return build_tail_audition_state(manifest_path)
