from __future__ import annotations

from pathlib import Path

from app.brand import DEFAULT_BRAND_LIBRARY, snapshot_brand, validate_brand_config
from tests.fixture_assets import make_brand_library


def test_approved_second_pressing_snapshot_is_project_owned_and_hash_pinned(tmp_path: Path) -> None:
    library = make_brand_library(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    config = snapshot_brand(project, library_path=library)
    assert config["enabled"] is True
    assert config["revision"] == "sp-lockup-v1"
    assert config["snapshot"]["immutableProjectAssets"] is True
    assert validate_brand_config(project, config) == []
    for role in ("monogram", "lockup", "vector", "watermark", "approvalManifest"):
        record = config["assets"][role]
        path = project / record["path"]
        assert path.is_file()
        assert DEFAULT_BRAND_LIBRARY not in path.parents
        assert record["sha256"]
    assert config["assets"]["watermark"]["path"] != config["assets"]["monogram"]["path"]
    assert config["assets"]["watermark"]["sha256"] == config["assets"]["monogram"]["sha256"]


def test_brand_snapshot_detects_project_asset_tampering(tmp_path: Path) -> None:
    library = make_brand_library(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    config = snapshot_brand(project, library_path=library)
    path = project / config["assets"]["lockup"]["path"]
    path.write_bytes(path.read_bytes() + b"tamper")
    assert any("lockup" in issue for issue in validate_brand_config(project, config))
