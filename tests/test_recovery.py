from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.catalogue import save_candidate_slots
from app.processing import create_album_job, disk_space_check, reconcile_jobs, run_album_job
from app.projects import load_manifest, open_project


class FakeSeparator:
    def __init__(self, source: Path, output_dir: Path):
        self.source = source
        self.output_dir = output_dir
        self.stem_output_contract = {"targetStem": "Instrumental", "expectedStemNames": ["Instrumental", "Vocals"]}

    def separate(self, _audio_file_path: str, _custom_output_names: dict[str, str] | None = None) -> list[str]:
        instrumental = self.output_dir / "fixture_(Instrumental).wav"
        vocals = self.output_dir / "fixture_(Vocals).wav"
        shutil.copyfile(self.source, instrumental)
        shutil.copyfile(self.source, vocals)
        return [str(vocals), str(instrumental)]


def setup_project(tmp_path: Path) -> tuple[Path, dict]:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "1 - track.wav").write_bytes(b"audio")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 1.0, "codec": "fake"})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidate = {"candidateId": "model:a", "type": "Model", "label": "Alpha", "engineIdentifier": "a.ckpt", "technicalIdentifier": "a.ckpt", "components": [], "algorithm": None, "cacheState": "Cached", "targetStem": "instrumental"}
    save_candidate_slots(manifest_path, {"A": candidate}, {"engine": {"version": "test"}, "candidates": [candidate]})
    return manifest_path, manifest


def fake_factory(manifest: dict, _candidate: dict, _cache: Path, output_dir: Path) -> FakeSeparator:
    return FakeSeparator(Path(manifest["tracks"][0]["sourcePath"]), output_dir)


def test_cache_requires_provenance_and_registered_file_integrity(tmp_path: Path) -> None:
    manifest_path, manifest = setup_project(tmp_path)
    job_id, state_path, _ = create_album_job(manifest_path)
    run_album_job(manifest_path, state_path, tmp_path / "models", separator_factory=lambda candidate, cache, output: fake_factory(manifest, candidate, cache, output), probe=lambda _path: {"durationSeconds": 1.0, "codec": "fake"})
    cached_job, cached_state_path, cached_state = create_album_job(manifest_path)
    assert cached_job != job_id
    assert cached_state["tasks"][0]["stage"] == "Complete"
    output = next(iter(load_manifest(manifest_path)["outputs"].values()))
    Path(output["path"]).write_bytes(b"tampered")
    _, invalid_state_path, invalid_state = create_album_job(manifest_path)
    assert invalid_state["tasks"][0]["stage"] == "Queued"
    assert cached_state_path.exists() and invalid_state_path.exists()


def test_reconcile_marks_abandoned_job_and_preserves_only_valid_complete_work(tmp_path: Path) -> None:
    manifest_path, _ = setup_project(tmp_path)
    _, state_path, state = create_album_job(manifest_path)
    state["status"] = "running"
    state["tasks"][0]["stage"] = "Processing"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    changed = reconcile_jobs(manifest_path)
    reconciled = json.loads(state_path.read_text(encoding="utf-8"))
    assert changed == [state["jobId"]]
    assert reconciled["status"] == "failed"
    assert reconciled["stage"] == "Interrupted"
    assert reconciled["tasks"][0]["stage"] == "Queued"


def test_low_disk_guard_is_actionable_without_estimating_output_size(tmp_path: Path, monkeypatch) -> None:
    manifest_path, _ = setup_project(tmp_path)
    monkeypatch.setattr("app.processing.shutil.disk_usage", lambda _path: type("Usage", (), {"free": 1})())
    result = disk_space_check(manifest_path, minimum_free_bytes=10)
    assert result["ready"] is False
    assert "low" in result["detail"]
    assert "size" not in result["detail"].lower()
