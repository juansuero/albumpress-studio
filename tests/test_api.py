from __future__ import annotations

from pathlib import Path
import wave

from fastapi.testclient import TestClient

from app.config import AppPaths
from app.main import create_app
from app.settings import save_settings


def test_health_and_settings_round_trip(tmp_path: Path) -> None:
    app_paths = AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")
    save_settings({"projectLibrary": str(tmp_path / "Library")}, app_paths)
    app = create_app(frontend_dist=tmp_path / "missing-dist", paths=app_paths)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok", "service": "albumpress-studio"}
        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["lastSection"] == "album"
        updated = client.patch("/api/settings/lastSection", json={"value": "compare"})
        assert updated.status_code == 200
        assert updated.json()["lastSection"] == "compare"


def test_production_spa_fallback_is_served(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>archive</main>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    app = create_app(frontend_dist=dist)
    with TestClient(app) as client:
        assert client.get("/").text == "<main>archive</main>"
        assert client.get("/compare").text == "<main>archive</main>"
        assert client.get("/assets/app.js").text == "console.log('ok')"


def test_album_project_api_opens_and_restores_manifest(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    with wave.open(str(source / "1 - intro.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)
    app_paths = AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")
    save_settings({"projectLibrary": str(tmp_path / "Library")}, app_paths)
    app = create_app(frontend_dist=tmp_path / "missing-dist", paths=app_paths)
    with TestClient(app) as client:
        opened = client.post("/api/projects/open", json={"sourcePath": str(source)})
        assert opened.status_code == 200
        assert opened.json()["tracks"][0]["title"] == "1 - intro"
        current = client.get("/api/projects/current")
        assert current.status_code == 200
        assert current.json()["projectId"] == opened.json()["projectId"]


def test_project_library_api_previews_lists_and_removes_recent_pointer(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    with wave.open(str(source / "track.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)
    library = tmp_path / "Library"
    app_paths = AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")
    save_settings({"projectLibrary": str(library)}, app_paths)
    app = create_app(frontend_dist=tmp_path / "missing-dist", paths=app_paths)
    with TestClient(app) as client:
        preview = client.post("/api/projects/preview", json={"sourcePath": str(source), "projectName": "Album"})
        assert preview.status_code == 200
        assert preview.json()["projectFolder"] == str(library / "Album")
        assert preview.json()["freeSpaceOk"] is True

        opened = client.post("/api/projects/open", json={"sourcePath": str(source), "projectName": "Album"})
        assert opened.status_code == 200
        assert opened.json()["projectFolder"] == str(library / "Album")
        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert listed.json()["projects"][0]["projectFolder"] == str(library / "Album")

        removed = client.post("/api/projects/remove-recent", json={"manifestPath": str(library / "Album" / "project.json")})
        assert removed.status_code == 200
        assert (library / "Album").is_dir()
        assert removed.json()["projects"]


def test_project_migration_api_reports_cost_without_copying(tmp_path: Path) -> None:
    source = tmp_path / "Album"
    source.mkdir()
    with wave.open(str(source / "track.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)
    app_paths = AppPaths(tmp_path / "data", tmp_path / "data" / "settings.json", tmp_path / "data" / "app.log", tmp_path / "data" / "models")
    save_settings({"projectLibrary": str(tmp_path / "Library")}, app_paths)
    app = create_app(frontend_dist=tmp_path / "missing-dist", paths=app_paths)
    with TestClient(app) as client:
        opened = client.post("/api/projects/open", json={"sourcePath": str(source), "projectName": "Album"})
        assert opened.status_code == 200
        project_folder = Path(opened.json()["projectFolder"])
        destination = tmp_path / "Permanent" / "Album"
        preview = client.post("/api/projects/migration-preview", json={"destinationPath": str(destination)})
        assert preview.status_code == 200
        assert preview.json()["sourceProjectFolder"] == str(project_folder)
        assert preview.json()["canMigrate"] is True
        assert not destination.exists()
