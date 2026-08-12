from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.video_render import VideoRenderError, _inside, _promote, _read_status, _validate_mp4, _validate_snapshot_current, _write_status, read_video_render_job, retry_synthetic_render, start_synthetic_render, stop_video_render_job


def test_render_asset_must_stay_inside_project_folder(tmp_path: Path) -> None:
    with pytest.raises(VideoRenderError, match="outside"):
        _inside(tmp_path, tmp_path / ".." / "outside.txt")


def test_invalid_mp4_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "invalid.mp4"
    output.write_bytes(b"not an mp4")
    snapshot = {"expected": {"width": 1920, "height": 1080, "fps": 30, "durationSeconds": 8, "videoCodec": "h264", "audioCodec": "aac", "audioSampleRate": 48000, "audioChannels": 2}}
    with pytest.raises(VideoRenderError):
        _validate_mp4(output, snapshot)


def test_backend_restart_marks_queued_job_interrupted(tmp_path: Path) -> None:
    manifest = tmp_path / "project.json"
    manifest.write_text("{}", encoding="utf-8")
    job_id = "abcdef123456"
    status_path = tmp_path / ".stem-comparison" / "video-jobs" / job_id / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"jobId": job_id, "status": "queued", "stage": "queued", "progress": 0}), encoding="utf-8")

    status = read_video_render_job(manifest, job_id, {})

    assert status["status"] == "interrupted"
    assert "backend restart" in status["message"]


def test_status_io_serializes_concurrent_reads_and_writes(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path, {"jobId": "abcdef123456", "status": "queued"})

    def write_statuses() -> None:
        for index in range(30):
            _write_status(status_path, {"jobId": "abcdef123456", "status": "running", "progress": index / 30})

    def read_statuses() -> None:
        for _ in range(30):
            assert _read_status(status_path)["jobId"] == "abcdef123456"

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda action: action(), (write_statuses, read_statuses)))


def test_only_one_video_render_job_can_be_active(tmp_path: Path) -> None:
    manifest = tmp_path / "project.json"
    manifest.write_text("{}", encoding="utf-8")
    active_id = "123456abcdef"
    status_path = tmp_path / ".stem-comparison" / "video-jobs" / active_id / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"jobId": active_id, "status": "running"}), encoding="utf-8")

    with pytest.raises(VideoRenderError, match="already active"):
        start_synthetic_render(manifest, {})


def test_snapshot_fingerprint_change_blocks_promotion(tmp_path: Path) -> None:
    manifest = tmp_path / "project.json"
    config = tmp_path / "video" / "config.json"
    config.parent.mkdir()
    manifest.write_text("manifest", encoding="utf-8")
    config.write_text("config", encoding="utf-8")
    snapshot = {
        "codeFingerprint": "not-current",
        "fingerprints": {"projectManifest": "wrong", "configuration": "wrong"},
        "assets": {},
    }

    with pytest.raises(VideoRenderError, match="stale"):
        _validate_snapshot_current(tmp_path, snapshot)


def test_cancellation_marks_job_stopping_without_touching_previous_output(tmp_path: Path) -> None:
    manifest = tmp_path / "project.json"
    manifest.write_text("{}", encoding="utf-8")
    job_id = "abcdef123456"
    status_path = tmp_path / ".stem-comparison" / "video-jobs" / job_id / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"jobId": job_id, "status": "running", "stage": "rendering", "progress": 0.5}), encoding="utf-8")

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    process = FakeProcess()
    status = stop_video_render_job(manifest, job_id, {job_id: {"process": process, "statusPath": status_path, "cancelRequested": False}})

    assert status["status"] == "stopping"
    assert process.terminated is True


def test_retry_restarts_only_terminal_job(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "project.json"
    manifest.write_text("{}", encoding="utf-8")
    job_id = "abcdef123456"
    status_path = tmp_path / ".stem-comparison" / "video-jobs" / job_id / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"jobId": job_id, "status": "failed", "stage": "failed"}), encoding="utf-8")
    expected = {"jobId": "new-job", "status": "queued"}
    monkeypatch.setattr("app.video_render.start_synthetic_render", lambda project_manifest, processes: expected)

    assert retry_synthetic_render(manifest, job_id, {}) == expected


def test_successful_promotion_writes_versioned_manifest(tmp_path: Path) -> None:
    staging = tmp_path / "staging.mp4"
    staging.write_bytes(b"validated fixture")
    snapshot = {"kind": "synthetic-test"}
    validation = {"checks": {"fixture": True}, "ffprobe": {}, "sha256": "fixture", "bytes": staging.stat().st_size}

    output, manifest = _promote(tmp_path, "abcdef123456", staging, snapshot, validation)

    assert output == tmp_path / "video" / "renders" / "ticket-12" / "v1" / "album-landscape-smoke.mp4"
    assert manifest.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["validation"]["checks"]["fixture"] is True
