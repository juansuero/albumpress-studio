from __future__ import annotations

import struct
import shutil
import wave
from pathlib import Path

import pytest

import app.video as video
from app.projects import open_project, save_manifest


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)


def write_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1920, 1080) + b"\x08\x06\x00\x00\x00")


def configure_fixture(tmp_path: Path, monkeypatch) -> Path:
    source = tmp_path / "Source"
    source.mkdir()
    write_wav(source / "01 intro.wav")
    manifest = open_project(source, tmp_path / "Project")
    root = Path(manifest["projectFolder"])
    track = manifest["tracks"][0]
    output_id = f"album:{track['trackId']}:A"
    output_name = "A_01_intro_Instrumental.wav"
    (root / "outputs" / "album").mkdir(parents=True)
    (root / "final").mkdir()
    (root / "outputs" / "album" / output_name).write_bytes(b"valid output")
    (root / "final" / "01_intro_Instrumental.wav").write_bytes(b"valid output")
    manifest["outputs"] = {output_id: {"outputId": output_id, "trackId": track["trackId"], "slot": "A", "candidateId": "model:hq5", "path": f"outputs/album/{output_name}", "status": "valid", "semanticStatus": "confirmed", "durationSeconds": 1.0, "fileFingerprint": "fixture-fingerprint"}}
    manifest["selections"] = {track["trackId"]: {"trackId": track["trackId"], "trackTitle": track["title"], "slot": "A", "candidateId": "model:hq5", "outputId": output_id, "outputFingerprint": "fixture-fingerprint", "selectedAt": "2026-08-09T00:00:00Z"}}
    save_manifest(manifest, root)

    approved = tmp_path / "approved"
    approved.mkdir()
    artwork = approved / "art.png"
    write_png(artwork)
    display = approved / "display.ttf"
    display.write_bytes(b"\x00\x01\x00\x00fixture")
    utility = approved / "utility.woff2"
    utility.write_bytes(b"wOF2fixture")
    monkeypatch.setattr(video, "APPROVED_ARTWORK", artwork)
    monkeypatch.setattr(video, "APPROVED_DISPLAY_FONT", display)
    monkeypatch.setattr(video, "APPROVED_UTILITY_FONT", utility)
    return root / "project.json"


def test_video_configuration_copies_assets_snapshots_selections_and_calculates_timeline(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    state = video.configure_video(manifest_path, {"artist": "Fixture Artist", "album": "Fixture Album"})

    assert state["ready"] is True
    assert state["composition"]["durationInFrames"] == 30
    assert state["composition"]["timeline"][0]["startFrame"] == 0
    assert state["composition"]["timeline"][0]["durationInFrames"] == 30
    assert state["config"]["tracks"][0]["outputId"].startswith("album:")
    assert state["config"]["colors"]["marker"] == "#D99A59"
    assert state["config"]["assets"]["artwork"]["path"].startswith("video/assets/")
    assert not any("tmp" in str(value).casefold() for value in state["config"]["assets"].values())


def test_video_configuration_snapshots_besley_pair_and_external_artwork(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    artwork = tmp_path / "western-background.png"
    write_png(artwork)
    regular = tmp_path / "Besley-VariableFont_wght.ttf"
    italic = tmp_path / "Besley-Italic-VariableFont_wght.ttf"
    regular.write_bytes(b"\x00\x01\x00\x00besley-regular")
    italic.write_bytes(b"\x00\x01\x00\x00besley-italic")

    state = video.configure_video(
        manifest_path,
        {
            "artist": "Fixture Artist",
            "album": "Fixture Album",
            "typography": {"displayFontFamily": "Besley", "utilityFontFamily": "Atkinson Hyperlegible Next"},
            "displayFontPath": str(regular),
            "displayFontItalicPath": str(italic),
            "artworkSourcePath": str(artwork),
        },
    )

    assert state["ready"] is True
    assert state["config"]["assets"]["displayFont"]["family"] == "Besley"
    assert state["config"]["assets"]["displayFontItalic"]["family"] == "Besley"
    assert state["config"]["assets"]["artwork"]["path"].endswith("western-swing-cattle-background-more-sky-2560x1440.png")
    assert state["composition"]["inputProps"]["displayFontItalicUrl"] == "/api/video/assets/display-font-italic"


def test_video_configuration_requires_user_owned_assets_when_defaults_are_absent(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(video, "APPROVED_ARTWORK", tmp_path / "missing-artwork.png")

    with pytest.raises(video.ProjectError, match="ALBUMPRESS_DEFAULT_ARTWORK"):
        video.configure_video(manifest_path, {"artist": "Fixture Artist", "album": "Fixture Album"})

    artwork = tmp_path / "user-artwork.png"
    write_png(artwork)
    monkeypatch.setattr(video, "APPROVED_DISPLAY_FONT", tmp_path / "missing-display.ttf")
    with pytest.raises(video.ProjectError, match="ALBUMPRESS_DEFAULT_DISPLAY_FONT"):
        video.configure_video(
            manifest_path,
            {"artist": "Fixture Artist", "album": "Fixture Album", "artworkSourcePath": str(artwork)},
        )


def test_video_configuration_blocks_changed_selection_without_fallback(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    video.configure_video(manifest_path, {"artist": "Fixture Artist", "album": "Fixture Album"})
    manifest = video.load_manifest(manifest_path)
    track_id = manifest["tracks"][0]["trackId"]
    manifest["outputs"][manifest["selections"][track_id]["outputId"]]["fileFingerprint"] = "changed"
    save_manifest(manifest, Path(manifest["projectFolder"]))

    state = video.build_video_state(manifest_path)

    assert state["ready"] is False
    assert any("stale" in issue.casefold() for issue in state["issues"])
    assert "candidate" not in " ".join(state["issues"]).casefold()


def test_video_configuration_covers_long_titles_and_missing_artwork(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    manifest = video.load_manifest(manifest_path)
    manifest["tracks"][0]["title"] = "01-01 " + ("A very long western title " * 12)
    save_manifest(manifest, Path(manifest["projectFolder"]))
    configured = video.configure_video(manifest_path, {"artist": "Fixture Artist", "album": "Fixture Album"})
    assert configured["ready"] is True
    assert len(configured["config"]["tracks"][0]["title"]) > 200

    artwork_path = Path(configured["projectFolder"]) / "video" / "assets" / "little-songs-background-user.png"
    artwork_path.unlink()
    blocked = video.build_video_state(manifest_path)
    assert blocked["ready"] is False
    assert any("artwork" in issue.casefold() for issue in blocked["issues"])


def test_video_configuration_survives_moved_folder_and_missing_source(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    video.configure_video(manifest_path, {"artist": "Fixture Artist", "album": "Fixture Album"})
    manifest = video.load_manifest(manifest_path)
    manifest["sourceFolder"] = str(tmp_path / "source-no-longer-mounted")
    save_manifest(manifest, Path(manifest["projectFolder"]))
    moved_root = tmp_path / "Moved Project"
    shutil.copytree(manifest_path.parent, moved_root)

    moved = video.build_video_state(moved_root / "project.json")

    assert moved["ready"] is True
    assert moved["projectFolder"] == str(moved_root)


def test_video_configuration_flags_low_contrast_palette(tmp_path: Path, monkeypatch) -> None:
    manifest_path = configure_fixture(tmp_path, monkeypatch)
    state = video.configure_video(manifest_path, {"artist": "Fixture Artist", "album": "Fixture Album", "colors": {"primary": "#17151A", "secondary": "#17151A", "accent": "#17151A", "scrim": "#17151A"}})

    assert state["ready"] is False
    assert any("contrast" in issue.casefold() for issue in state["issues"])
