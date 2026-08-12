from __future__ import annotations

import math
import struct
import wave
import zlib
from pathlib import Path

import app.video_preparation as preparation


def write_png(path: Path, width: int, height: int, color: tuple[int, int, int, int] = (90, 70, 50, 255)) -> None:
    row = bytes(color) * width
    raw = b"".join(b"\x00" + row for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def write_tone_tail(path: Path, tone_seconds: float, silence_seconds: float, *, amplitude: float = 0.35) -> None:
    sample_rate = 8000
    tone_samples = int(tone_seconds * sample_rate)
    silence_samples = int(silence_seconds * sample_rate)
    frames = bytearray()
    for index in range(tone_samples):
        value = int(math.sin(index * 2 * math.pi * 440 / sample_rate) * amplitude * 32767)
        frames.extend(struct.pack("<h", value))
    frames.extend(b"\0\0" * silence_samples)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


def test_trailing_silence_confident_trim_and_short_tail_is_preserved(tmp_path: Path) -> None:
    long_tail = tmp_path / "long-tail.wav"
    short_tail = tmp_path / "short-tail.wav"
    write_tone_tail(long_tail, 3, 4)
    write_tone_tail(short_tail, 3, 1.5)

    trimmed = preparation.analyze_trailing_silence(long_tail)
    preserved = preparation.analyze_trailing_silence(short_tail)

    assert trimmed["status"] == "confident"
    assert 3.8 < trimmed["detectedTailSeconds"] < 4.2
    assert 2.8 < trimmed["proposedRemovalSeconds"] < 3.2
    assert 3.8 < trimmed["effectiveDurationSeconds"] < 4.2
    assert preserved["status"] == "no_trim"
    assert preserved["proposedRemovalSeconds"] == 0
    assert preserved["effectiveDurationSeconds"] == preserved["originalDurationSeconds"]


def test_disagreement_and_unsafe_proposal_fail_closed_to_review(tmp_path: Path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.wav"
    fixture.write_bytes(b"fixture")
    monkeypatch.setattr(preparation, "_probe_duration", lambda _path: 30.0)

    def fake_pass(_path: Path, noise_db: float, _minimum: float) -> dict[str, object]:
        tail = 10.0 if noise_db == -35 else 5.0
        return {"noiseDb": noise_db, "silenceStarts": [30.0 - tail], "silenceEnds": [30.0]}

    result = preparation.analyze_trailing_silence(fixture, runner=fake_pass)

    assert result["status"] == "review"
    assert result["proposedRemovalSeconds"] == 0
    assert result["effectiveDurationSeconds"] == 30.0
    assert "disagree" in result["reason"]


def test_windowed_hysteresis_preserves_decay_and_ignores_isolated_click(tmp_path: Path) -> None:
    levels = []
    for index in range(24):
        if index < 12:
            rms_db = -20.0
        elif index < 16:
            rms_db = [-48.0, -54.0, -58.0, -62.0][index - 12]
        elif index == 18:
            rms_db = -20.0
        else:
            rms_db = -90.0
        levels.append({"startSeconds": index * 0.25, "endSeconds": (index + 1) * 0.25, "rmsDb": rms_db, "peakDb": rms_db})

    result = preparation._windowed_analysis(
        levels,
        6.0,
        window_seconds=0.25,
        sample_rate=8000,
        enter_threshold_db=-45.0,
        exit_threshold_db=-60.0,
        release_hold_seconds=0.75,
        minimum_active_seconds=0.5,
        tail_padding_seconds=1.0,
        minimum_trim_seconds=0.75,
        max_proposed_removal_seconds=20.0,
        analyzer_version=preparation.SILENCE_ANALYZER_VERSION,
    )

    assert result["status"] == "confident"
    assert result["windowedAnalysis"]["tailClassification"] == "decay_or_reverb_then_silence"
    assert result["windowedAnalysis"]["ignoredIsolatedEvents"] == 1
    assert result["effectiveDurationSeconds"] < 6.0


def test_artwork_derivation_is_deterministic_cached_and_invalidated(tmp_path: Path) -> None:
    artwork = tmp_path / "artwork.png"
    write_png(artwork, 1600, 900)
    record = {"path": artwork.name}

    first = preparation.prepare_artwork(tmp_path, record)
    first_hash = first["effective"]["sha256"]
    second = preparation.prepare_artwork(tmp_path, record)

    assert first["derived"] is True
    assert first["effective"]["width"] == 3840
    assert first["effective"]["height"] == 2160
    assert second["cacheHit"] is True
    assert second["effective"]["sha256"] == first_hash

    write_png(artwork, 1600, 900, (12, 24, 36, 255))
    invalidated = preparation.prepare_artwork(tmp_path, record)

    assert invalidated["sourceSha256"] != first["sourceSha256"]
    assert invalidated["effective"]["path"] != first["effective"]["path"]

    original = preparation.prepare_artwork(tmp_path, record, "Original")
    assert original["derived"] is False
    assert original["effective"]["path"] == artwork.name


def test_prepare_video_config_rebuilds_effective_duration_and_audio_cache(tmp_path: Path) -> None:
    artwork = tmp_path / "artwork.png"
    final = tmp_path / "final.wav"
    write_png(artwork, 1600, 900)
    write_tone_tail(final, 3, 4)
    config = {"schemaVersion": 2, "assets": {"artwork": {"path": artwork.name}}, "preparation": preparation.default_preparation(), "provenance": {}}
    tracks = [{"trackId": "track-1", "sequence": 1, "title": "01-01 Test", "durationSeconds": 7.0, "outputId": "output-1", "slot": "A", "fileFingerprint": "manifest-fingerprint", "finalPath": final.name}]

    prepared = preparation.prepare_video_config(tmp_path, config, tracks)
    prepared_track = prepared["tracks"][0]

    assert prepared_track["originalDurationSeconds"] == 7.0
    assert 3.8 < prepared_track["effectiveDurationSeconds"] < 4.2
    assert prepared["preparation"]["summary"]["tracksAnalyzed"] == 1
    assert prepared["preparation"]["summary"]["secondsRemoved"] > 1.8
    assert prepared["assets"]["effectiveArtwork"]["width"] == 3840

    final.write_bytes(final.read_bytes() + b"\0")
    refreshed = preparation.prepare_video_config(tmp_path, prepared, tracks)
    assert refreshed["tracks"][0]["finalFingerprint"] != prepared["tracks"][0]["finalFingerprint"]
