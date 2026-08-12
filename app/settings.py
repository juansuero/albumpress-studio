from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppPaths, ensure_app_paths
from .json_store import atomic_write_json, read_json
from .projects import default_project_library


def default_settings() -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "lastProjectManifest": None,
        "lastSection": "album",
        "projectLibrary": str(default_project_library()),
        "recentProjects": [],
    }


def load_settings(paths: AppPaths | None = None) -> dict[str, Any]:
    resolved = paths or ensure_app_paths()
    defaults = default_settings()
    value = read_json(resolved.settings_file, defaults)
    if not isinstance(value, dict):
        return defaults
    return {**defaults, **value}


def save_settings(value: dict[str, Any], paths: AppPaths | None = None) -> dict[str, Any]:
    resolved = paths or ensure_app_paths()
    normalized = {**default_settings(), **value, "schemaVersion": 2}
    atomic_write_json(resolved.settings_file, normalized)
    return normalized


def update_setting(name: str, value: Any, paths: AppPaths | None = None) -> dict[str, Any]:
    current = load_settings(paths)
    current[name] = value
    return save_settings(current, paths)
