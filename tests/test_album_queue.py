from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.catalogue import save_candidate_slots
from app.processing import create_album_job, run_album_job
from app.projects import open_project


class FakeAlbumSeparator:
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


@pytest.mark.parametrize("track_count,candidate_count", [(1, 2), (4, 3), (6, 4)])
def test_album_queue_is_track_first_and_completes_small_fake_workloads(tmp_path: Path, track_count: int, candidate_count: int) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    for index in range(1, track_count + 1):
        (source / f"{index:02d} - track.wav").write_bytes(b"fake audio")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 1.0, "codec": "fake"})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidates = [{
        "candidateId": f"model:{slot.lower()}",
        "type": "Model",
        "label": f"Candidate {slot} with a deliberately long readable label",
        "engineIdentifier": f"{slot.lower()}.ckpt",
        "technicalIdentifier": f"{slot.lower()}.ckpt",
        "components": [],
        "algorithm": None,
        "cacheState": "Cached",
        "targetStem": "instrumental",
        "slot": slot,
    } for slot in "ABCD"[:candidate_count]]
    save_candidate_slots(manifest_path, {candidate["slot"]: candidate for candidate in candidates}, {"candidates": candidates})
    job_id, state_path, _ = create_album_job(manifest_path)

    def fake_factory_for_queue(candidate: dict, _cache: Path, output_dir: Path) -> FakeAlbumSeparator:
        return FakeAlbumSeparator(next(source.glob("*.wav")), output_dir)

    run_album_job(manifest_path, state_path, tmp_path / "models", separator_factory=fake_factory_for_queue, probe=lambda _path: {"durationSeconds": 1.0, "codec": "fake"})
    state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    assert state["jobId"] == job_id
    assert state["status"] == "complete"
    assert len(state["tasks"]) == track_count * candidate_count
    assert all(task["stage"] == "Complete" for task in state["tasks"])
    assert [(task["trackId"], task["slot"]) for task in state["tasks"][:candidate_count]] == [(manifest["tracks"][0]["trackId"], slot) for slot in "ABCD"[:candidate_count]]


def test_reusable_fast_candidate_is_loaded_once_for_album(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "01 - one.wav").write_bytes(b"one")
    (source / "02 - two.wav").write_bytes(b"two")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 1.0, "codec": "fake"})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidate = {"candidateId": "model:UVR-MDX-NET-Inst_HQ_5.onnx", "type": "Model", "label": "HQ5", "engineIdentifier": "UVR-MDX-NET-Inst_HQ_5.onnx", "technicalIdentifier": "UVR-MDX-NET-Inst_HQ_5.onnx", "components": [], "targetStem": "instrumental", "slot": "A", "reusableLoadedModel": True}
    save_candidate_slots(manifest_path, {"A": candidate}, {"candidates": [candidate]})
    _, state_path, _ = create_album_job(manifest_path)
    factory_calls = 0

    def factory(_candidate: dict, _cache: Path, output_dir: Path) -> FakeAlbumSeparator:
        nonlocal factory_calls
        factory_calls += 1
        return FakeAlbumSeparator(source / "01 - one.wav", output_dir)

    run_album_job(manifest_path, state_path, tmp_path / "models", separator_factory=factory, probe=lambda _path: {"durationSeconds": 1.0, "codec": "fake"})
    state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "complete"
    assert factory_calls == 1
