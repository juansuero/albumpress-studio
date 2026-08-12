from __future__ import annotations

import shutil
import struct
import wave
from pathlib import Path

from app.catalogue import save_candidate_slots
from app.processing import _capture_returned_outputs, _configured_stem_contract, _pick_instrumental, _validate_returned_stems, create_calibration_job, run_calibration_job
from app.projects import open_project


def write_wav(path: Path, *, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000 * seconds)


def write_level_wav(path: Path, level: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(struct.pack("<h", level) * 8000)


class FakeSeparator:
    def __init__(self, candidate: dict, output_dir: Path):
        self.candidate = candidate
        self.output_dir = output_dir
        self.stem_output_contract = {"targetStem": "Instrumental", "expectedStemNames": ["Instrumental", "Vocals"]}

    def separate(self, audio_file_path: str, _custom_output_names: dict[str, str] | None = None) -> list[str]:
        if self.candidate["slot"] == "B":
            raise RuntimeError("fake candidate failure")
        instrumental = self.output_dir / "fixture_(Instrumental).wav"
        vocals = self.output_dir / "fixture_(Vocals).wav"
        shutil.copyfile(audio_file_path, instrumental)
        shutil.copyfile(audio_file_path, vocals)
        return [str(vocals), str(instrumental)]


def test_calibration_uses_fake_separator_contract_and_registers_only_valid_outputs(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "1 - intro.wav")
    manifest = open_project(source)
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidates = [
        {"candidateId": "model:a", "type": "Model", "label": "Alpha", "engineIdentifier": "a.ckpt", "technicalIdentifier": "a.ckpt", "components": [], "algorithm": None, "cacheState": "Cached", "slot": "A", "targetStem": "instrumental"},
        {"candidateId": "model:b", "type": "Model", "label": "Beta", "engineIdentifier": "b.ckpt", "technicalIdentifier": "b.ckpt", "components": [], "algorithm": None, "cacheState": "Cached", "slot": "B", "targetStem": "instrumental"},
    ]
    save_candidate_slots(manifest_path, {"A": candidates[0], "B": candidates[1]}, {"candidates": candidates})
    job_id, state_path, _ = create_calibration_job(manifest_path)

    def fake_factory(candidate: dict, _cache: Path, output_dir: Path) -> FakeSeparator:
        return FakeSeparator(candidate, output_dir)

    run_calibration_job(manifest_path, state_path, tmp_path / "models", separator_factory=fake_factory)

    state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    refreshed = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert state["jobId"] == job_id
    assert state["status"] == "complete"
    assert [task["stage"] for task in state["tasks"]] == ["Complete", "Failed"]
    assert state["tasks"][0]["elapsedSeconds"] >= 0
    assert refreshed["outputs"][f"calibration:{refreshed['tracks'][0]['trackId']}:A"]["status"] == "valid"
    assert refreshed["outputs"][f"calibration:{refreshed['tracks'][0]['trackId']}:A"]["format"] == "WAV"
    returned_outputs = refreshed["tasks"][f"{job_id}:A"]["returnedOutputs"]
    assert [item["role"] for item in returned_outputs] == ["complementary", "target"]
    project_root = manifest_path.parent
    assert all((project_root / item["preservedPath"]).is_file() for item in returned_outputs)
    assert len(refreshed["outputs"][f"calibration:{refreshed['tracks'][0]['trackId']}:A"]["provenance"]["returnedOutputs"]) == 2
    assert list((Path(manifest["outputFolder"]) / "outputs" / "calibration").glob("A_01_1-intro_Instrumental.wav"))
    assert not list((Path(manifest["outputFolder"]) / ".stem-comparison" / "tmp" / job_id).glob("**/*"))
    assert any(event["stage"] == "Validating" for event in state["events"])


def test_semantic_selector_survives_swapped_filenames(tmp_path: Path) -> None:
    class SwappedFilenameSeparator:
        def __init__(self) -> None:
            self.output_dir = tmp_path / "returned"
            self.output_dir.mkdir()
            self.stem_output_contract = {"targetStem": "Instrumental", "expectedStemNames": ["Instrumental", "Vocals"]}

        def separate(self, _audio_file_path: str, _custom_output_names=None) -> list[str]:
            actual_instrumental = self.output_dir / "returned_(Vocals).wav"
            actual_vocals = self.output_dir / "returned_(Instrumental).wav"
            write_level_wav(actual_instrumental, 250)
            write_level_wav(actual_vocals, 900)
            self.returned_stem_names = {
                str(actual_instrumental): "Instrumental",
                str(actual_vocals): "Vocals",
            }
            return [str(actual_vocals), str(actual_instrumental)]

    separator = SwappedFilenameSeparator()
    returned = separator.separate("source.wav", None)
    records = _capture_returned_outputs(separator, returned, tmp_path / "preserved", slot="A", sequence=1, title="fixture")
    validated = _validate_returned_stems(separator, {"targetStem": "instrumental"}, records)
    selected = _pick_instrumental(validated)
    assert next(record for record in validated if record["role"] == "target")["engineName"].endswith("_(Vocals).wav")
    with wave.open(str(selected), "rb") as handle:
        assert struct.unpack("<h", handle.readframes(1))[0] == 250


def test_semantic_contract_comes_from_cached_model_config(tmp_path: Path) -> None:
    model_cache = tmp_path / "models"
    model_cache.mkdir()
    (model_cache / "config_demo.yaml").write_text(
        "training:\n  instruments: [vocals, other]\n  target_instrument: other\n",
        encoding="utf-8",
    )
    contract = _configured_stem_contract(
        {"type": "Model", "engineIdentifier": "demo.ckpt", "targetStem": "instrumental", "components": []},
        model_cache,
    )
    assert contract is not None
    assert contract["targetStem"] == "Instrumental"
    assert contract["expectedStemNames"] == ["Instrumental", "Vocals"]
    assert contract["source"] == "cached-model-config"
