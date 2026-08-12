from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from app.exporting import build_export_plan, export_album
from app.listening import approve_and_select_all
from app.processing import fingerprint_file
from app.projects import ProjectError, load_manifest, open_project, save_manifest


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)


def add_output(manifest: dict, track_id: str, slot: str, output: Path) -> None:
    output_id = f"album:{track_id}:{slot}"
    manifest["outputs"][output_id] = {"outputId": output_id, "trackId": track_id, "slot": slot, "candidateId": f"model:{slot.lower()}", "path": str(output), "status": "valid", "semanticStatus": "confirmed", "semanticValidation": {"role": "target", "canonicalStem": "Instrumental"}, "format": "WAV", "durationSeconds": 1.0, "fileFingerprint": fingerprint_file(output), "candidate": {"label": f"Candidate {slot}"}}


def test_export_blocks_incomplete_and_copies_only_valid_selections(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "1 - intro.wav")
    write_wav(source / "2 - verse.wav")
    manifest = open_project(source)
    output_root = Path(manifest["outputFolder"]) / "outputs" / "album"
    output_root.mkdir(parents=True)
    for track in manifest["tracks"]:
        output = output_root / f"{track['trackId']}.wav"
        write_wav(output)
        add_output(manifest, track["trackId"], "A", output)
    first = manifest["tracks"][0]
    manifest["selections"][first["trackId"]] = {"trackId": first["trackId"], "slot": "A", "outputId": f"album:{first['trackId']}:A"}
    manifest["selectionSummary"] = "01 — 1 - intro: Candidate A"
    save_manifest(manifest, Path(manifest["outputFolder"]))
    manifest_path = Path(manifest["outputFolder"]) / "project.json"

    plan = build_export_plan(manifest_path)
    assert plan["ready"] is False
    assert plan["missing"][0]["trackId"] == manifest["tracks"][1]["trackId"]
    with pytest.raises(ProjectError):
        export_album(manifest_path)

    second = manifest["tracks"][1]
    manifest = load_manifest(manifest_path)
    manifest["selections"][second["trackId"]] = {"trackId": second["trackId"], "slot": "A", "outputId": f"album:{second['trackId']}:A"}
    save_manifest(manifest, Path(manifest["outputFolder"]))
    unrelated = Path(manifest["outputFolder"]) / "final" / "keep-me.txt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("unrelated", encoding="utf-8")
    result = export_album(manifest_path)
    assert result["status"] == "current"
    assert unrelated.read_text(encoding="utf-8") == "unrelated"
    assert len(list((Path(result["destinationFolder"])).glob("*_Instrumental.wav"))) == 2
    repeat = export_album(manifest_path)
    assert [item["destinationPath"] for item in repeat["items"]] == [item["destinationPath"] for item in result["items"]]


def test_single_candidate_approval_unlocks_export_only_at_10_of_10(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    for index in range(1, 11):
        write_wav(source / f"{index:02d} - track.wav")
    manifest = open_project(source)
    candidate = {"candidateId": "model:hq5", "type": "Model", "label": "HQ5", "engineIdentifier": "hq5.onnx", "technicalIdentifier": "hq5.onnx", "components": [], "algorithm": None, "cacheState": "Cached", "targetStem": "instrumental", "slot": "A"}
    manifest["candidates"] = [candidate]
    output_root = Path(manifest["outputFolder"]) / "outputs" / "album"
    output_root.mkdir(parents=True)
    for track in manifest["tracks"]:
        output = output_root / f"{track['trackId']}.wav"
        write_wav(output)
        add_output(manifest, track["trackId"], "A", output)
        record = manifest["outputs"][f"album:{track['trackId']}:A"]
        record["candidateId"] = candidate["candidateId"]
        record["candidate"] = candidate
        record["semanticStatus"] = "pending"
    save_manifest(manifest, Path(manifest["outputFolder"]))
    manifest_path = Path(manifest["outputFolder"]) / "project.json"

    assert build_export_plan(manifest_path)["ready"] is False
    result = approve_and_select_all(str(manifest_path))
    assert result["approved"] == 10
    plan = build_export_plan(manifest_path)
    assert plan["ready"] is True
    assert len(plan["items"]) == 10
    exported = export_album(manifest_path)
    assert exported["status"] == "current"
    assert len(exported["items"]) == 10
