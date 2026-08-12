from __future__ import annotations

from pathlib import Path

import pytest

from app.catalogue import apply_fast_default_slots, discover_catalogue, estimate_candidate_seconds, save_candidate_slots
from app.config import AppPaths
from app.projects import ProjectError, open_project, save_manifest


class FakeSeparator:
    def __init__(self, _cache: Path, *, presets: dict | None = None, models: dict | None = None):
        self.presets = presets if presets is not None else {"instrumental_full": {"name": "Full", "models": ["a.ckpt"], "algorithm": "avg_wave"}}
        self.models = models if models is not None else {"VR": {"Model A": {"filename": "a.ckpt", "download_files": ["a.ckpt"], "stems": ["instrumental", "vocals"]}}}

    def list_supported_model_files(self):
        return self.models

    def list_ensemble_presets(self):
        return self.presets


def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")


def test_catalogue_normalizes_models_presets_and_recommendations(tmp_path: Path) -> None:
    result = discover_catalogue(paths=paths(tmp_path), separator_factory=lambda cache: FakeSeparator(cache))
    assert result["live"] is True
    assert result["counts"] == {"models": 1, "presets": 1, "total": 2}
    assert result["recommendations"][0]["candidateId"] == "model:UVR-MDX-NET-Inst_HQ_5.onnx"
    assert result["recommendations"][0]["available"] is False
    assert next(item for item in result["recommendations"] if item["candidateId"] == "preset:instrumental_full")["available"] is True
    assert {candidate["type"] for candidate in result["candidates"]} == {"Model", "Preset"}


def test_catalogue_models_only_reports_unavailable_recommendations(tmp_path: Path) -> None:
    result = discover_catalogue(paths=paths(tmp_path), separator_factory=lambda cache: FakeSeparator(cache, presets={}))
    assert result["live"] is True
    assert result["recommendations"][0]["available"] is False
    assert result["candidates"][0]["type"] == "Model"


def test_catalogue_failure_is_not_presented_as_current(tmp_path: Path) -> None:
    result = discover_catalogue(paths=paths(tmp_path), separator_factory=lambda _cache: (_ for _ in ()).throw(RuntimeError("engine offline")))
    assert result["live"] is False
    assert result["status"] == "unavailable"
    assert result["candidates"] == []


def test_candidate_slots_reject_duplicates_and_unknown_ids(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "1.wav").write_bytes(b"source")
    output = open_project(source, probe=lambda _path: {"durationSeconds": 1})["outputFolder"]
    manifest_path = Path(output) / "project.json"
    catalogue = {"candidates": [{"candidateId": "model:a", "type": "Model", "label": "A"}]}
    candidate = catalogue["candidates"][0]
    with pytest.raises(ProjectError):
        save_candidate_slots(manifest_path, {"A": candidate, "B": candidate}, catalogue)
    with pytest.raises(ProjectError):
        save_candidate_slots(manifest_path, {"A": {"candidateId": "model:missing"}}, catalogue)


def test_fast_default_preserves_old_outputs_as_invalid_and_uses_local_estimate(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "1.wav").write_bytes(b"source")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 120.0})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    manifest["candidates"] = [{"candidateId": "preset:instrumental_balanced", "slot": "A"}]
    manifest["outputs"] = {"album:old": {"outputId": "album:old", "slot": "A", "candidateId": "preset:instrumental_balanced", "status": "valid"}}
    manifest["selections"] = {manifest["tracks"][0]["trackId"]: {"outputId": "album:old", "slot": "A"}}
    save_manifest(manifest, Path(manifest["outputFolder"]))
    fast = {"candidateId": "model:UVR-MDX-NET-Inst_HQ_5.onnx", "type": "Model", "label": "HQ5", "engineIdentifier": "UVR-MDX-NET-Inst_HQ_5.onnx", "benchmark": {"secondsPerSourceSecond": 2.0, "modelLoadSeconds": 10.0}, "reusableLoadedModel": True}
    updated = apply_fast_default_slots(manifest_path, {"engine": {"version": "test"}, "candidates": [fast]})
    assert updated["candidates"][0]["candidateId"] == fast["candidateId"]
    assert updated["candidates"][0]["slot"] == "A"
    assert updated["outputs"]["album:old"]["status"] == "invalid"
    assert updated["selections"] == {}
    assert updated["fastPath"]["deepCandidateId"] == "preset:instrumental_full"
    assert estimate_candidate_seconds(fast, 120.0) == 250.0
