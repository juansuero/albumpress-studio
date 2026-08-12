from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

import app.video_package as video_package
from app.video_render import _code_fingerprint


def write_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1280, 720) + b"\x08\x06\x00\x00\x00")


def test_chapter_time_and_cumulative_track_timing() -> None:
    assert video_package._chapter_time(0) == "00:00:00"
    assert video_package._chapter_time(3661.9) == "01:01:01"
    timed = video_package._timed_tracks({"expected": {"fps": 30}, "tracks": [{"trackId": "one", "sequence": 1, "title": "One", "durationSeconds": 4}, {"trackId": "two", "sequence": 2, "title": "Two", "durationSeconds": 4}]})
    assert [item["startSeconds"] for item in timed] == [0.0, 4.0]
    assert [item["startFrame"] for item in timed] == [0, 120]


def test_human_track_title_removes_source_prefix_and_keeps_natural_separator() -> None:
    assert video_package._human_track_title("01-01 Prairie Evening_Sagebush Waltz") == "Prairie Evening / Sagebush Waltz"
    assert video_package._human_track_title("01-07 Cow_Calf Blue Yodel") == "Cow / Calf Blue Yodel"
    assert video_package._human_track_title("02 - I Ride an Old Paint + Leavin' Cheyenne") == "I Ride an Old Paint / Leavin' Cheyenne"


def test_timed_tracks_preserve_renderer_boundaries() -> None:
    timed = video_package._timed_tracks({"expected": {"fps": 30}, "tracks": [{"trackId": "one", "sequence": 1, "title": "One", "durationSeconds": 1.01, "startFrame": 0, "durationInFrames": 30}, {"trackId": "two", "sequence": 2, "title": "Two", "durationSeconds": 1.01, "startFrame": 30, "durationInFrames": 31}]})
    assert [(item["startFrame"], item["durationInFrames"]) for item in timed] == [(0, 30), (30, 31)]


def test_package_brand_manifest_is_snapshot_based() -> None:
    snapshot = {
        "props": {"brand": {"enabled": True, "profile": "second-pressing", "revision": "sp-lockup-v1", "openingSeconds": 1.75, "closingSeconds": 2.5, "thumbnailStamp": {"enabled": False}}},
        "assets": {"brand-lockup": {"relativePath": "video/assets/brand/sp-lockup-v1/lockup.png", "sha256": "lockup"}, "audio-1": {"relativePath": "audio.wav"}},
    }
    manifest = video_package._brand_manifest(snapshot)
    assert manifest["enabled"] is True
    assert manifest["revision"] == "sp-lockup-v1"
    assert set(manifest["assets"]) == {"brand-lockup"}
    assert manifest["timing"] == {"openingSeconds": 1.75, "closingSeconds": 2.5}


def test_real_package_versions_increment_after_legacy_package(tmp_path: Path) -> None:
    packages = tmp_path / "video" / "packages"
    (packages / "real-job123-old").mkdir(parents=True)
    (packages / "real-job123-old" / "manifest.json").write_text("{}", encoding="utf-8")
    assert video_package._next_real_package_version(tmp_path, "job123") == 2
    (packages / "real-job123-v2").mkdir(parents=True)
    (packages / "real-job123-v2" / "manifest.json").write_text("{}", encoding="utf-8")
    assert video_package._next_real_package_version(tmp_path, "job123") == 3


def test_package_requires_a_validated_render(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text("manifest", encoding="utf-8")
    with pytest.raises(video_package.VideoPackageError, match="No completed ticket-12 render"):
        video_package.generate_synthetic_video_package(project)


def test_synthetic_package_contains_only_registered_artifacts(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project.json"
    config = tmp_path / "video" / "config.json"
    render_dir = tmp_path / "video" / "renders" / "ticket-12" / "v1"
    render_dir.mkdir(parents=True)
    project.write_text("manifest", encoding="utf-8")
    config.write_text("config", encoding="utf-8")
    video_path = render_dir / "album-landscape-smoke.mp4"
    video_path.write_bytes(b"synthetic mp4 fixture")
    snapshot = {
        "projectFolder": str(tmp_path),
        "projectManifest": "project.json",
        "configuration": "video/config.json",
        "codeFingerprint": _code_fingerprint(),
        "fingerprints": {"projectManifest": hashlib.sha256(project.read_bytes()).hexdigest(), "configuration": hashlib.sha256(config.read_bytes()).hexdigest()},
        "assets": {},
        "expected": {"fps": 30, "durationSeconds": 8},
        "settings": {"concurrency": 2},
        "props": {"artist": "Synthetic Artist", "album": "Synthetic Album", "descriptionNotes": ""},
        "tracks": [{"trackId": "one", "sequence": 1, "title": "Éxito / Éxito " + ("Long " * 8), "durationSeconds": 4}, {"trackId": "two", "sequence": 2, "title": "Éxito / Éxito " + ("Long " * 8), "durationSeconds": 4}],
    }
    render_manifest = {"schemaVersion": 1, "jobId": "abcdef123456", "kind": "synthetic-two-track-boundary-smoke", "outputPath": "video/renders/ticket-12/v1/album-landscape-smoke.mp4", "snapshot": snapshot, "validation": {"checks": {"fixture": True}, "sha256": hashlib.sha256(video_path.read_bytes()).hexdigest()}}
    (render_dir / "render-manifest.json").write_text(json.dumps(render_manifest), encoding="utf-8")

    def fake_thumbnail(input_path: Path) -> None:
        write_png(input_path.parent / "thumbnail.png")

    monkeypatch.setattr(video_package, "_render_thumbnail", fake_thumbnail)
    result = video_package.generate_synthetic_video_package(project, notes="Fixture note")

    package = Path(result["packageFolder"])
    assert {item.name for item in package.iterdir()} == {"album-video.mp4", "thumbnail.png", "chapters.txt", "description.txt", "manifest.json"}
    chapters = (package / "chapters.txt").read_text(encoding="utf-8")
    assert "00:00:04 Éxito / Éxito" in chapters
    assert chapters.count("Éxito / Éxito") == 2
    assert "Fixture note" in (package / "description.txt").read_text(encoding="utf-8")
    assert video_package.read_current_video_package(project)["ready"] is True
    config.write_text("changed", encoding="utf-8")
    blocked = video_package.read_current_video_package(project)
    assert blocked["ready"] is False
    assert any("stale" in issue.casefold() for issue in blocked["issues"])
