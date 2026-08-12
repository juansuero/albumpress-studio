from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.listening import approve_and_select, approve_and_select_all, confirm_output_semantics, invalidate_output, select_candidate, update_loop
from app.processing import fingerprint_file
from app.projects import ProjectError, open_project, save_manifest


def test_loop_and_selection_persist_atomically_and_block_tampered_outputs(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "track.wav").write_bytes(b"source")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 10.0})
    output = Path(manifest["outputFolder"]) / "outputs" / "album" / "A_01_track_Instrumental.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"valid output")
    track_id = manifest["tracks"][0]["trackId"]
    manifest["outputs"]["album:track:A"] = {"outputId": "album:track:A", "trackId": track_id, "slot": "A", "candidateId": "model:a", "candidate": {"label": "Alpha"}, "path": str(output), "status": "valid", "semanticStatus": "pending", "semanticValidation": {"role": "target", "canonicalStem": "Instrumental"}, "fileFingerprint": fingerprint_file(output)}
    manifest["tasks"]["album:task"] = {"taskId": "album:task", "outputId": "album:track:A", "semanticStatus": "pending"}
    save_manifest(manifest, Path(manifest["outputFolder"]))
    manifest_path = Path(manifest["outputFolder"]) / "project.json"

    updated = update_loop(str(manifest_path), track_id, {"inSeconds": 2, "outSeconds": 5, "enabled": True})
    assert updated["loops"][track_id]["enabled"] is True
    with pytest.raises(ProjectError, match="semantic"):
        select_candidate(str(manifest_path), track_id, "A", "album:track:A")
    confirmed = confirm_output_semantics(str(manifest_path), "album:track:A")
    assert confirmed["outputs"]["album:track:A"]["semanticStatus"] == "confirmed"
    assert confirmed["tasks"]["album:task"]["semanticStatus"] == "confirmed"
    selected = select_candidate(str(manifest_path), track_id, "A", "album:track:A")
    assert selected["selections"][track_id]["slot"] == "A"
    assert "Candidate A" in selected["selectionSummary"]
    with pytest.raises(ProjectError):
        update_loop(str(manifest_path), track_id, {"inSeconds": 8, "outSeconds": 3, "enabled": True})

    output.write_bytes(b"tampered")
    with pytest.raises(ProjectError, match="integrity"):
        select_candidate(str(manifest_path), track_id, "A", "album:track:A")


def _single_candidate_project(tmp_path: Path, track_count: int) -> tuple[Path, dict]:
    source = tmp_path / "Album"
    source.mkdir()
    for index in range(1, track_count + 1):
        (source / f"{index:02d} - track.wav").write_bytes(f"source-{index}".encode())
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 10.0})
    candidate = {"candidateId": "model:hq5", "type": "Model", "label": "HQ5", "engineIdentifier": "hq5.onnx", "technicalIdentifier": "hq5.onnx", "components": [], "algorithm": None, "cacheState": "Cached", "targetStem": "instrumental", "slot": "A"}
    manifest["candidates"] = [candidate]
    for index, track in enumerate(manifest["tracks"], start=1):
        output = Path(manifest["outputFolder"]) / "outputs" / "album" / f"A_{index:02d}_track_Instrumental.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"output-{index}".encode())
        output_id = f"album:{track['trackId']}:A"
        manifest["outputs"][output_id] = {"outputId": output_id, "taskId": f"job:{track['trackId']}:A", "trackId": track["trackId"], "slot": "A", "candidateId": "model:hq5", "candidate": candidate, "path": str(output), "status": "valid", "semanticStatus": "pending", "semanticValidation": {"role": "target", "canonicalStem": "Instrumental"}, "fileFingerprint": fingerprint_file(output)}
    save_manifest(manifest, Path(manifest["outputFolder"]))
    return Path(manifest["outputFolder"]) / "project.json", manifest


def test_approve_and_select_is_atomic_and_idempotent(tmp_path: Path) -> None:
    manifest_path, manifest = _single_candidate_project(tmp_path, 1)
    track_id = manifest["tracks"][0]["trackId"]
    output_id = f"album:{track_id}:A"

    first = approve_and_select(str(manifest_path), track_id, "A", output_id)
    second = approve_and_select(str(manifest_path), track_id, "A", output_id)

    assert first["outputs"][output_id]["semanticStatus"] == "confirmed"
    assert second["selections"][track_id]["outputId"] == output_id
    assert len(second["semanticConfirmations"]) == 1
    assert second["selectionHistory"] == []
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["outputs"][output_id]["semanticStatus"] == "confirmed"
    assert persisted["selections"][track_id]["outputId"] == output_id


def test_approve_and_select_all_reports_and_excludes_invalid_outputs(tmp_path: Path) -> None:
    manifest_path, manifest = _single_candidate_project(tmp_path, 4)
    tracks = manifest["tracks"]
    invalid_id = f"album:{tracks[2]['trackId']}:A"
    missing_id = f"album:{tracks[3]['trackId']}:A"
    manifest["outputs"][invalid_id]["status"] = "invalid"
    manifest["outputs"][missing_id]["path"] = str(Path(manifest["outputFolder"]) / "outputs" / "album" / "missing.wav")
    save_manifest(manifest, manifest_path.parent)

    result = approve_and_select_all(str(manifest_path))

    assert result["pending"] == 2
    assert result["approved"] == 2
    assert len(result["results"]) == 4
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(persisted["selections"]) == 2
    assert persisted["outputs"][invalid_id]["status"] == "invalid"
    assert missing_id not in persisted["selections"]


def test_reject_output_is_separate_and_removes_invalid_selection(tmp_path: Path) -> None:
    manifest_path, manifest = _single_candidate_project(tmp_path, 1)
    track_id = manifest["tracks"][0]["trackId"]
    output_id = f"album:{track_id}:A"
    approve_and_select(str(manifest_path), track_id, "A", output_id)

    rejected = invalidate_output(str(manifest_path), output_id, "Rejected during listening review.")

    assert rejected["outputs"][output_id]["status"] == "invalid"
    assert rejected["outputs"][output_id]["semanticStatus"] == "rejected"
    assert rejected["selections"] == {}
    assert rejected["export"]["status"] == "invalidated"
