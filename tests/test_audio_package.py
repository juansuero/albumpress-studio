from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from app import audio_package
from app.audio_package import AudioPackageCancelled, generate_audio_package, read_current_audio_package, read_audio_job


def _tone(path: Path, frequency: float, seconds: float = 2.0, sample_rate: int = 44_100) -> None:
    frames = bytearray()
    for index in range(int(seconds * sample_rate)):
        value = int(13_000 * math.sin(2 * math.pi * frequency * index / sample_rate))
        frames.extend(struct.pack("<hh", value, value))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def _snapshot(root: Path) -> dict:
    first = root / "01.wav"
    second = root / "02.wav"
    _tone(first, 440)
    _tone(second, 660)
    return {
        "schemaVersion": 1,
        "recipeVersion": "full-album-mp3-v1",
        "projectFolder": str(root),
        "projectManifestSha256": "fixture-manifest",
        "timelineFingerprint": "fixture-timeline",
        "fps": 30,
        "fadeInSeconds": 0.1,
        "fadeOutSeconds": 0.2,
        "expectedDurationSeconds": 3.0,
        "tracks": [
            {"trackId": "one", "sequence": 1, "title": "01-01 First_Song", "finalPath": "01.wav", "fileFingerprint": "fixture", "startFrame": 0, "durationInFrames": 45, "startSeconds": 0.0, "sourceStartSeconds": 0.0, "durationSeconds": 1.5},
            {"trackId": "two", "sequence": 2, "title": "02-02 Second Song", "finalPath": "02.wav", "fileFingerprint": "fixture", "startFrame": 45, "durationInFrames": 45, "startSeconds": 1.5, "sourceStartSeconds": 0.0, "durationSeconds": 1.5},
        ],
        "metadata": {"title": "Synthetic Album", "artist": "Synthetic Artist", "album": "Synthetic Album", "albumArtist": "Synthetic Artist", "year": "2026", "genre": "Instrumental", "comment": "Fixture"},
        "cover": {"choice": "none", "sourcePath": None, "sha256": None},
        "settings": {"bitrate": 320000, "sampleRate": 44100, "channels": 2, "durationToleranceSeconds": 0.12},
        "inputFingerprint": "fixture-input",
        "optionsFingerprint": "fixture-options",
    }


def test_synthetic_audio_package_uses_order_fades_tags_and_boundaries(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    result = generate_audio_package(tmp_path, snapshot, package_id="mp3-aaaaaaaaaaaa-v1", package_version=1)

    assert result["ready"] is True
    manifest = result["manifest"]
    assert manifest["validation"]["checks"]
    assert all(manifest["validation"]["checks"].values())
    assert manifest["validation"]["ffprobe"]["streams"][0]["codec_name"] == "mp3"
    assert manifest["validation"]["ffprobe"]["streams"][0]["sample_rate"] == "44100"
    assert manifest["validation"]["ffprobe"]["streams"][0]["channels"] == 2
    assert "00:00:00.000 01 First / Song" in (tmp_path / "audio" / "packages" / "mp3-aaaaaaaaaaaa-v1" / "chapters.txt").read_text(encoding="utf-8")
    cue = (tmp_path / "audio" / "packages" / "mp3-aaaaaaaaaaaa-v1" / "album.cue").read_text(encoding="utf-8")
    assert "TRACK 01 AUDIO" in cue and "INDEX 01 00:00:00" in cue
    assert manifest["analysis"]["integratedLufs"] is not None


def test_audio_package_cache_is_read_only_and_corruption_fails_closed(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    first = generate_audio_package(tmp_path, snapshot, package_id="mp3-bbbbbbbbbbbb-v1", package_version=1)
    cached = read_current_audio_package(tmp_path, package_id=first["packageId"])
    assert cached["ready"] is True
    output = tmp_path / "audio" / "packages" / first["packageId"] / "album-mix.mp3"
    output.write_bytes(output.read_bytes() + b"corrupt")
    blocked = read_current_audio_package(tmp_path, package_id=first["packageId"])
    assert blocked["ready"] is False
    assert any("changed" in issue for issue in blocked["issues"])


def test_audio_job_recovery_removes_unpromoted_staging(tmp_path: Path) -> None:
    job_id = "c" * 32
    job_dir = tmp_path / ".stem-comparison" / "audio-package-jobs" / job_id
    (job_dir / "staging" / "package").mkdir(parents=True)
    (job_dir / "staging" / "package" / "partial.mp3").write_bytes(b"partial")
    status_path = job_dir / "status.json"
    status_path.write_text(json.dumps({"jobId": job_id, "status": "running", "stage": "assembling"}), encoding="utf-8")

    # Exercise the public recovery contract through a synthetic project manifest.
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    recovered = read_audio_job(project, job_id, {})
    assert recovered["status"] == "interrupted"
    assert not (job_dir / "staging").exists()


def test_audio_package_cancellation_never_promotes_partial_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot(tmp_path)

    def cancel(*args, **kwargs):
        raise AudioPackageCancelled("synthetic cancellation")

    monkeypatch.setattr(audio_package, "_run_ffmpeg", cancel)
    with pytest.raises(AudioPackageCancelled):
        generate_audio_package(tmp_path, snapshot, package_id="mp3-cccccccccccc-v1", package_version=1)

    assert not (tmp_path / "audio" / "packages" / "mp3-cccccccccccc-v1").exists()


def test_audio_job_retry_reuses_saved_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = "d" * 32
    job_dir = tmp_path / ".stem-comparison" / "audio-package-jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text(json.dumps({"jobId": job_id, "status": "failed"}), encoding="utf-8")
    (job_dir / "input.json").write_text(json.dumps({"options": {"title": "Retry title", "coverChoice": "none"}}), encoding="utf-8")
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_start(manifest: Path, processes: dict, options: dict, *, force: bool) -> dict:
        captured.update({"manifest": manifest, "options": options, "force": force})
        return {"status": "queued"}

    monkeypatch.setattr(audio_package, "start_audio_export", fake_start)
    result = audio_package.retry_audio_job(project, job_id, {})

    assert result["status"] == "queued"
    assert captured["options"] == {"title": "Retry title", "coverChoice": "none"}
    assert captured["force"] is True
