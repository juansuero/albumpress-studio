from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from app.project_migration import migration_preview, migrate_project
from app.exporting import build_export_plan
from app.processing import ProcessingError, create_album_job
from app.projects import ProjectError, create_project, fingerprint_file, save_manifest


def write_wav(path: Path, *, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000 * seconds)


def make_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "track.wav")
    manifest = create_project(source, project_name="Portable", project_library=tmp_path / "Library")
    root = Path(manifest["projectFolder"])
    output = root / "outputs" / "album" / "A_01_track_Instrumental.wav"
    output.parent.mkdir(parents=True)
    write_wav(output)
    (root / "artwork").mkdir()
    (root / "artwork" / "cover.txt").write_text("user artifact", encoding="utf-8")
    manifest["outputs"] = {
        "album:track:A": {
            "outputId": "album:track:A",
            "path": str(output),
            "status": "valid",
            "durationSeconds": 1.0,
            "fileFingerprint": fingerprint_file(output),
        }
    }
    save_manifest(manifest, root)
    return source, root, output


def test_migration_preview_reports_exact_cost_and_preservation_plan(tmp_path: Path) -> None:
    _source, root, _output = make_project(tmp_path)
    preview = migration_preview(root / "project.json", tmp_path / "Permanent" / "Portable")
    assert preview["sourceProjectFolder"] == str(root.resolve())
    assert preview["destinationProjectFolder"].endswith("Permanent\\Portable") or preview["destinationProjectFolder"].endswith("Permanent/Portable")
    assert preview["artifactCount"] >= 3
    assert preview["registeredArtifactCount"] == 1
    assert preview["canMigrate"] is True


def test_migration_copy_validate_switch_preserves_original_and_exact_artifacts(tmp_path: Path) -> None:
    _source, root, _output = make_project(tmp_path)
    destination = tmp_path / "Permanent" / "Portable"
    result = migrate_project(root / "project.json", destination)
    assert result["status"] == "promoted"
    assert result["originalRetained"] is True
    assert destination.is_dir()
    assert (destination / "artwork" / "cover.txt").read_text(encoding="utf-8") == "user artifact"
    persisted = json.loads((destination / "project.json").read_text(encoding="utf-8"))
    assert persisted["outputFolder"] == "."
    assert persisted["outputs"]["album:track:A"]["path"] == "outputs/album/A_01_track_Instrumental.wav"
    assert fingerprint_file(destination / "outputs" / "album" / "A_01_track_Instrumental.wav") == persisted["outputs"]["album:track:A"]["fileFingerprint"]


def test_legacy_manifest_is_intentionally_migrated_without_changing_registered_bytes(tmp_path: Path) -> None:
    _source, root, output = make_project(tmp_path)
    legacy = json.loads((root / "project.json").read_text(encoding="utf-8"))
    legacy["schemaVersion"] = 1
    legacy["outputFolder"] = str(root)
    legacy.pop("projectFolder", None)
    legacy.pop("projectPaths", None)
    legacy["outputs"]["album:track:A"]["path"] = str(output)
    (root / "project.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    original_hash = fingerprint_file(output)

    destination = tmp_path / "Legacy" / "Portable"
    migrate_project(root / "project.json", destination)
    migrated = json.loads((destination / "project.json").read_text(encoding="utf-8"))
    assert migrated["schemaVersion"] == 2
    assert migrated["outputFolder"] == "."
    assert fingerprint_file(destination / "outputs" / "album" / "A_01_track_Instrumental.wav") == original_hash
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["schemaVersion"] == 1


def test_legacy_manifest_rebases_export_paths(tmp_path: Path) -> None:
    _source, root, output = make_project(tmp_path)
    final = root / "final" / "legacy.wav"
    final.parent.mkdir(parents=True)
    final.write_bytes(output.read_bytes())
    legacy = json.loads((root / "project.json").read_text(encoding="utf-8"))
    legacy["schemaVersion"] = 1
    legacy["export"] = {
        "status": "current",
        "destinationFolder": str(final.parent),
        "items": {"track-1": {"sourcePath": str(output), "destinationPath": str(final)}},
    }
    (root / "project.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")

    destination = tmp_path / "LegacyExport" / "Portable"
    migrate_project(root / "project.json", destination)
    migrated = json.loads((destination / "project.json").read_text(encoding="utf-8"))
    item = migrated["export"]["items"]["track-1"]
    assert migrated["export"]["destinationFolder"] == "final"
    assert item["sourcePath"] == "outputs/album/A_01_track_Instrumental.wav"
    assert item["destinationPath"] == "final/legacy.wav"


def test_migration_failure_and_cancellation_leave_original_and_no_partial_destination(tmp_path: Path) -> None:
    _source, root, _output = make_project(tmp_path)
    destination = tmp_path / "Failed" / "Portable"
    calls = 0

    def failing_copy(source: str, target: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated copy failure")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(Path(source).read_bytes())

    with pytest.raises(OSError):
        migrate_project(root / "project.json", destination, copy_file=failing_copy)
    assert root.is_dir()
    assert not destination.exists()
    assert not list(destination.parent.glob(f".{destination.name}.migration-*"))

    cancelled_destination = tmp_path / "Cancelled" / "Portable"
    with pytest.raises(ProjectError, match="cancelled"):
        migrate_project(root / "project.json", cancelled_destination, should_cancel=lambda: True)
    assert root.is_dir()
    assert not cancelled_destination.exists()


def test_missing_source_preserves_export_but_blocks_processing_until_relinked(tmp_path: Path) -> None:
    source, root, output = make_project(tmp_path)
    manifest = json.loads((root / "project.json").read_text(encoding="utf-8"))
    track_id = manifest["tracks"][0]["trackId"]
    output_id = "album:track:A"
    manifest["outputs"][output_id].update({"outputId": output_id, "trackId": track_id, "semanticStatus": "confirmed"})
    manifest["selections"] = {track_id: {"trackId": track_id, "trackTitle": "track", "slot": "A", "candidateId": "model:a", "outputId": output_id, "outputFingerprint": fingerprint_file(output)}}
    manifest["candidates"] = [{"candidateId": "model:a", "slot": "A", "label": "A"}]
    save_manifest(manifest, root)
    source.rename(tmp_path / "Disconnected")

    plan = build_export_plan(root / "project.json")
    assert plan["ready"] is True
    with pytest.raises(ProcessingError, match="source Album is unavailable"):
        create_album_job(root / "project.json")
