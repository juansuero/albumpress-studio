from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

from app.catalogue import save_candidate_slots
from app.exporting import export_album
from app.listening import confirm_output_semantics, select_candidate, update_loop
from app.processing import create_album_job, create_calibration_job, run_album_job, run_calibration_job
from app.projects import open_project


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)


class FakeSeparator:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.stem_output_contract = {"targetStem": "Instrumental", "expectedStemNames": ["Instrumental", "Vocals"]}

    def separate(self, audio_file_path: str, _custom_output_names=None) -> list[str]:
        instrumental = self.output_dir / "fixture_(Instrumental).wav"
        vocals = self.output_dir / "fixture_(Vocals).wav"
        shutil.copyfile(audio_file_path, instrumental)
        shutil.copyfile(audio_file_path, vocals)
        return [str(vocals), str(instrumental)]


def test_audio_mvp_deterministic_end_to_end(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "1 - intro.wav")
    write_wav(source / "2 - verse.wav")
    manifest = open_project(source)
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidates = [{"candidateId": f"model:{slot.lower()}", "type": "Model", "label": f"Candidate {slot}", "engineIdentifier": f"{slot.lower()}.ckpt", "technicalIdentifier": f"{slot.lower()}.ckpt", "components": [], "algorithm": None, "cacheState": "Cached", "targetStem": "instrumental"} for slot in "AB"]
    catalogue = {"engine": {"version": "test"}, "candidates": candidates}
    save_candidate_slots(manifest_path, {slot: {**candidate, "slot": slot} for slot, candidate in zip("AB", candidates)}, catalogue)
    factory = lambda _candidate, _cache, output_dir: FakeSeparator(output_dir)

    _, calibration_state, _ = create_calibration_job(manifest_path)
    run_calibration_job(manifest_path, calibration_state, tmp_path / "models", separator_factory=factory)
    _, album_state, _ = create_album_job(manifest_path)
    run_album_job(manifest_path, album_state, tmp_path / "models", separator_factory=factory)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for track in manifest["tracks"]:
        update_loop(str(manifest_path), track["trackId"], {"inSeconds": 0.1, "outSeconds": 0.5, "enabled": True})
        confirm_output_semantics(str(manifest_path), f"album:{track['trackId']}:A")
        select_candidate(str(manifest_path), track["trackId"], "A")
    result = export_album(manifest_path)
    assert result["status"] == "current"
    assert len(result["items"]) == 2
    assert all(Path(item["destinationPath"]).is_file() for item in result["items"])
