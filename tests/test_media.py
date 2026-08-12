from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppPaths
from app.main import create_app
from app.projects import open_project, save_manifest
from app.settings import save_settings


def test_registered_project_media_serves_safe_byte_ranges(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "track.wav").write_bytes(b"source")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 1.0})
    output = Path(manifest["outputFolder"]) / "outputs" / "album" / "A_01_track_Instrumental.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"0123456789")
    manifest["outputs"]["album:track:A"] = {"outputId": "album:track:A", "trackId": manifest["tracks"][0]["trackId"], "slot": "A", "path": str(output), "status": "valid", "durationSeconds": 1.0}
    save_manifest(manifest, Path(manifest["outputFolder"]))
    paths = AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")
    save_settings({"lastProjectManifest": str(Path(manifest["outputFolder"]) / "project.json"), "lastSection": "compare"}, paths)
    client = TestClient(create_app(frontend_dist=tmp_path / "dist", paths=paths))
    response = client.get(f"/api/projects/media/{manifest['tracks'][0]['trackId']}/A", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_invalid_processing_output_is_not_served(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    (source / "track.wav").write_bytes(b"source")
    manifest = open_project(source, probe=lambda _path: {"durationSeconds": 1.0})
    output = Path(manifest["outputFolder"]) / "outputs" / "album" / "A_01_track_Instrumental.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"invalid")
    track_id = manifest["tracks"][0]["trackId"]
    output_id = f"album:{track_id}:A"
    manifest["outputs"][output_id] = {"outputId": output_id, "trackId": track_id, "slot": "A", "path": str(output), "status": "invalid"}
    save_manifest(manifest, Path(manifest["outputFolder"]))
    job_state = Path(manifest["outputFolder"]) / ".stem-comparison" / "jobs" / "album-job" / "status.json"
    job_state.parent.mkdir(parents=True)
    job_state.write_text(json.dumps({"tasks": [{"trackId": track_id, "slot": "A", "outputId": output_id}]}), encoding="utf-8")
    paths = AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")
    save_settings({"lastProjectManifest": str(Path(manifest["outputFolder"]) / "project.json"), "lastSection": "compare"}, paths)
    client = TestClient(create_app(frontend_dist=tmp_path / "dist", paths=paths))
    response = client.get(f"/api/process/album/album-job/outputs/{track_id}/A")
    assert response.status_code == 404
