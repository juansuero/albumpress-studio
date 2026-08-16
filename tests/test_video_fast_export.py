from __future__ import annotations

import json
from pathlib import Path

from app.video_fast_export import SILENCE_RMS, _audio_plan, _boundary_is_silent, _promote_fast, _rms, retry_fast_render


def test_fast_audio_plan_uses_frame_authoritative_durations_and_boundaries(tmp_path: Path) -> None:
    (tmp_path / "final").mkdir()
    first = tmp_path / "final" / "one.wav"
    second = tmp_path / "final" / "two.wav"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    snapshot = {
        "expected": {"fps": 30, "frameCount": 300, "durationSeconds": 10},
        "tracks": [
            {"sequence": 1, "durationInFrames": 150, "startFrame": 0, "audioKey": "audio-1"},
            {"sequence": 2, "durationInFrames": 150, "startFrame": 150, "audioKey": "audio-2"},
        ],
        "assets": {
            "audio-1": {"relativePath": "final/one.wav"},
            "audio-2": {"relativePath": "final/two.wav"},
        },
    }

    plan = _audio_plan(snapshot, tmp_path, tmp_path / "audio.m4a")

    assert plan["boundaryFrames"] == [150]
    assert plan["totalSeconds"] == 10
    assert "atrim=start=0:end=5.000000" in plan["filterGraph"]
    assert "afade=t=in:st=0:d=1" in plan["filterGraph"]
    assert "afade=t=out:st=8.000000:d=2" in plan["filterGraph"]


def test_fast_audio_continuity_uses_source_silence_as_the_boundary_baseline() -> None:
    assert _rms([0, 0, 1, -1]) < 8
    assert _rms([100, -100, 100, -100]) >= 8


def test_fast_boundary_silence_is_relative_to_source_quiet_level() -> None:
    source_quiet = 6.09
    assert source_quiet < SILENCE_RMS
    assert _boundary_is_silent(13.44, source_quiet)
    assert _boundary_is_silent(5.8, source_quiet)
    assert _boundary_is_silent(6.09, source_quiet)


def test_fast_boundary_silence_still_flags_real_content_inserted_in_silence() -> None:
    source_quiet = 6.09
    assert not _boundary_is_silent(50.0, source_quiet)
    assert not _boundary_is_silent(200.0, source_quiet)


def test_fast_boundary_silence_preserves_content_detection_when_source_is_loud() -> None:
    source_loud = 34.23
    assert source_loud >= SILENCE_RMS
    assert not _boundary_is_silent(33.7, source_loud)
    assert not _boundary_is_silent(13.44, source_loud)
    assert _boundary_is_silent(5.0, source_loud)


def test_fast_promotion_is_versioned_and_preserves_previous_render(tmp_path: Path) -> None:
    existing = tmp_path / "video" / "renders" / "ticket-20-fast" / "v1"
    existing.mkdir(parents=True)
    previous = existing / "album-video-fast.mp4"
    previous.write_bytes(b"previous-valid-render")
    job_dir = tmp_path / ".stem-comparison" / "video-jobs" / "abcdef123456"
    muxed = job_dir / "staging" / "muxed.mp4"
    muxed.parent.mkdir(parents=True)
    muxed.write_bytes(b"new-render")

    output, manifest = _promote_fast(
        tmp_path,
        "abcdef123456",
        muxed,
        {"kind": "synthetic-fast-export", "expected": {"frameCount": 300}},
        {"sha256": "new", "checks": {"all": True}},
        {"totalSeconds": 10},
        {"selectedOutputTarget": "web-fs"},
        {"telemetry": {"available": True}},
    )

    assert output == tmp_path / "video" / "renders" / "ticket-20-fast" / "v2" / "album-video-fast.mp4"
    assert output.read_bytes() == b"new-render"
    assert previous.read_bytes() == b"previous-valid-render"
    assert json.loads(manifest.read_text(encoding="utf-8"))["mode"] == "fast"


def test_fast_retry_restarts_only_terminal_fast_jobs(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "project.json"
    manifest.write_text("{}", encoding="utf-8")
    status_path = tmp_path / ".stem-comparison" / "video-jobs" / "fedabc123456" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"jobId": "fedabc123456", "status": "failed", "mode": "fast", "sourceKind": "synthetic"}), encoding="utf-8")
    expected = {"jobId": "retry-job", "status": "queued", "mode": "fast"}
    monkeypatch.setattr("app.video_fast_export.start_fast_render", lambda project_manifest, processes, *, synthetic: expected)

    assert retry_fast_render(manifest, "fedabc123456", {}) == expected
