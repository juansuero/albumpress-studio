from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_DATA_NAME = "StemComparison"


def _windows_local_app_data() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local"


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    settings_file: Path
    log_file: Path
    model_cache: Path


def app_paths() -> AppPaths:
    data_dir = Path(os.environ.get("ALBUMPRESS_DATA_DIR") or os.environ.get("STEM_COMPARISON_DATA_DIR") or _windows_local_app_data() / APP_DATA_NAME)
    model_cache = Path(
        os.environ.get("ALBUMPRESS_MODEL_CACHE") or os.environ.get("STEM_COMPARISON_MODEL_CACHE") or data_dir / "models"
    )
    return AppPaths(
        data_dir=data_dir,
        settings_file=data_dir / "settings.json",
        log_file=data_dir / "app.log",
        model_cache=model_cache,
    )


def ensure_app_paths() -> AppPaths:
    paths = app_paths()
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.model_cache.mkdir(parents=True, exist_ok=True)
    return paths
