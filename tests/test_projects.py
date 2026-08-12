from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

import pytest

from app.projects import ProjectError, ProjectReadOnlyError, atomic_write_json, create_project, default_project_library, discover_project_manifests, open_project, open_project_manifest, relink_source, resolve_project_creation, save_manifest


def write_wav(path: Path, *, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000 * seconds)


def test_open_project_naturally_sorts_and_ignores_subfolders(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "10 - outro.wav")
    write_wav(source / "2 - verse.wav")
    write_wav(source / "1 - intro.wav")
    (source / "cover.jpg").write_bytes(b"not audio")
    nested = source / "bonus"
    nested.mkdir()
    write_wav(nested / "0 - nested.wav")

    manifest = open_project(source)

    assert [track["title"] for track in manifest["tracks"]] == ["1 - intro", "2 - verse", "10 - outro"]
    assert [track["sequence"] for track in manifest["tracks"]] == [1, 2, 3]
    assert manifest["tracks"][0]["durationSeconds"] == 1.0
    assert manifest["unsupportedFiles"] == [{"name": "cover.jpg", "reason": "Unsupported file type"}]
    assert (Path(manifest["outputFolder"]) / "project.json").exists()
    assert not (source / "project.json").exists()


def test_output_folder_is_safe_and_manifest_migration_is_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "track.wav")
    with pytest.raises(ProjectError):
        open_project(source, source)
    with pytest.raises(ProjectError):
        open_project(source, source / "inside")

    output = tmp_path / "existing"
    output.mkdir()
    (output / "project.json").write_text(json.dumps({"schemaVersion": 99}), encoding="utf-8")
    with pytest.raises(ProjectReadOnlyError):
        open_project(source, output)


def test_atomic_write_replaces_complete_manifest(tmp_path: Path) -> None:
    target = tmp_path / "project.json"
    atomic_write_json(target, {"schemaVersion": 1, "tracks": []})
    assert json.loads(target.read_text(encoding="utf-8"))["schemaVersion"] == 1
    assert list(tmp_path.glob(".*.tmp")) == []


def test_rescan_reconciles_tracks_without_dropping_unchanged_state(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "1 - kept.wav")
    write_wav(source / "2 - removed.wav")
    initial = open_project(source)
    initial["outputs"][initial["tracks"][0]["trackId"]] = {"status": "Complete"}
    save_manifest(initial, Path(initial["outputFolder"]))

    (source / "2 - removed.wav").unlink()
    write_wav(source / "3 - added.wav")
    with (source / "1 - kept.wav").open("ab") as handle:
        handle.write(b"changed")
    reconciled = open_project(source)

    assert [track["title"] for track in reconciled["tracks"]] == ["1 - kept", "3 - added"]
    assert reconciled["tracks"][0]["sourceFingerprint"] != initial["tracks"][0]["sourceFingerprint"]
    assert reconciled["outputs"][initial["tracks"][0]["trackId"]] == {"status": "Complete"}


def test_project_library_preview_creation_and_relative_project_paths(tmp_path: Path) -> None:
    source = tmp_path / "Source Album"
    source.mkdir()
    write_wav(source / "01 - intro.wav")
    library = tmp_path / "Music" / "AlbumPress Studio Projects"

    preview = resolve_project_creation(source, project_name="My: Album", project_library=library)
    assert preview["projectFolder"] == str(library / "My- Album")
    assert preview["freeSpaceOk"] is True
    assert default_project_library().name == "AlbumPress Studio Projects"

    manifest = create_project(source, project_name="My: Album", project_library=library)
    root = Path(manifest["projectFolder"])
    output_path = root / "outputs" / "album" / "A_01_intro_Instrumental.wav"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"instrumental")
    returned_path = root / "outputs" / "album" / "returned" / "A_01_intro_Returned_01_Vocals.wav"
    returned_path.parent.mkdir(parents=True)
    returned_path.write_bytes(b"vocals")
    manifest["outputs"] = {"album:track:A": {"path": str(output_path), "provenance": {"returnedOutputs": [{"preservedPath": str(returned_path)}]}}}
    save_manifest(manifest, root)

    persisted = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert persisted["schemaVersion"] == 2
    assert persisted["projectFolder"] == "."
    assert persisted["outputFolder"] == "."
    assert persisted["outputs"]["album:track:A"]["path"] == "outputs/album/A_01_intro_Instrumental.wav"
    assert persisted["outputs"]["album:track:A"]["provenance"]["returnedOutputs"][0]["preservedPath"] == "outputs/album/returned/A_01_intro_Returned_01_Vocals.wav"


def test_moved_project_hydrates_relative_artifacts_and_relinks_exact_source(tmp_path: Path) -> None:
    source = tmp_path / "Original Album"
    source.mkdir()
    write_wav(source / "01 - intro.wav")
    library = tmp_path / "Library"
    manifest = create_project(source, project_name="Portable", project_library=library)
    root = Path(manifest["projectFolder"])
    output = root / "outputs" / "album" / "A_01_intro_Instrumental.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"final")
    manifest["outputs"] = {"album:one:A": {"path": str(output), "status": "valid"}}
    save_manifest(manifest, root)

    moved = tmp_path / "Moved" / root.name
    moved.parent.mkdir()
    shutil.move(str(root), str(moved))
    reopened = open_project_manifest(moved / "project.json")
    assert reopened["projectFolder"] == str(moved.resolve())
    assert reopened["outputs"]["album:one:A"]["path"] == str(output).replace(str(root), str(moved))

    relocated_source = tmp_path / "Relocated Source"
    shutil.copytree(source, relocated_source)
    source.rename(tmp_path / "Disconnected Source")
    relinked = relink_source(moved / "project.json", relocated_source)
    assert relinked["sourceFolder"] == str(relocated_source.resolve())
    assert relinked["sourceState"]["status"] == "available"


def test_project_discovery_is_pointer_only_for_recent_external_projects(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    write_wav(source / "track.wav")
    manifest = create_project(source, project_name="External", project_library=tmp_path / "Library")
    manifest_path = Path(manifest["projectFolder"]) / "project.json"
    settings = {"projectLibrary": str(tmp_path / "Library"), "recentProjects": [{"manifestPath": str(manifest_path)}], "lastProjectManifest": str(manifest_path)}
    listing = discover_project_manifests(settings)
    assert len(listing["projects"]) == 1
    assert listing["projects"][0]["origin"] == "library"
