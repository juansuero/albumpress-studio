from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .projects import ProjectError, utc_now


DEFAULT_BRAND_LIBRARY = Path(
    os.environ.get("ALBUMPRESS_BRAND_LIBRARY")
    or os.environ.get("STEM_COMPARISON_BRAND_LIBRARY")
    or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "StemComparison" / "branding" / "approved"
)
MONOGRAM_SOURCE_NAME = "second-pressing-monogram-brown-original-1600x1600.png"
OPENING_SECONDS = 1.75
CLOSING_SECONDS = 2.5


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dimensions(path: Path) -> tuple[int, int] | None:
    if path.suffix.casefold() != ".png":
        return None
    try:
        with path.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            if int.from_bytes(handle.read(4), "big") != 13 or handle.read(4) != b"IHDR":
                return None
            return int.from_bytes(handle.read(4), "big"), int.from_bytes(handle.read(4), "big")
    except (OSError, ValueError):
        return None


def default_brand_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "profile": "second-pressing",
        "revision": None,
        "libraryPath": str(DEFAULT_BRAND_LIBRARY),
        "assets": {},
        "timing": {"openingSeconds": OPENING_SECONDS, "closingSeconds": CLOSING_SECONDS},
        "thumbnailStamp": {"enabled": False, "corner": "top-left", "widthFraction": 0.045},
        "snapshot": None,
    }


def _manifest(library: Path) -> dict[str, Any]:
    path = library / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"The approved Second Pressing manifest is unreadable: {path}") from exc
    if not isinstance(value, dict) or value.get("status") != "approved":
        raise ProjectError("The Second Pressing brand manifest is not approved.")
    if not str(value.get("revision") or "").strip():
        raise ProjectError("The approved Second Pressing manifest has no revision.")
    return value


def _manifest_path(library: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = library / candidate
    return candidate.resolve(strict=False)


def _source_monogram(library: Path) -> Path:
    source = library.parents[2] / "source" / MONOGRAM_SOURCE_NAME
    if not source.is_file():
        raise ProjectError(f"The approved Second Pressing monogram source is missing: {source}")
    dimensions = _dimensions(source)
    if dimensions != (1600, 1600):
        raise ProjectError("The approved Second Pressing monogram must be a 1600×1600 PNG.")
    return source


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def _asset_record(root: Path, path: Path, *, role: str, source: Path) -> dict[str, Any]:
    dimensions = _dimensions(path)
    result: dict[str, Any] = {
        "role": role,
        "path": path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "mimeType": "image/svg+xml" if path.suffix.casefold() == ".svg" else ("application/json" if path.suffix.casefold() == ".json" else "image/png"),
        "sourcePath": str(source),
        "sourceSha256": _sha256(source),
    }
    if dimensions:
        result.update({"width": dimensions[0], "height": dimensions[1]})
    return result


def _valid_asset(root: Path, record: Any) -> bool:
    if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
        return False
    try:
        path = (root / str(record["path"])).resolve(strict=False)
        path.relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return path.is_file() and _sha256(path) == record["sha256"]


def validate_brand_config(root: Path, config: Any) -> list[str]:
    if not isinstance(config, dict) or not bool(config.get("enabled")):
        return []
    issues: list[str] = []
    if config.get("profile") != "second-pressing":
        issues.append("Video brand profile is unsupported.")
    if not str(config.get("revision") or "").strip():
        issues.append("Video brand snapshot has no revision.")
    assets = config.get("assets") if isinstance(config.get("assets"), dict) else {}
    for key in ("monogram", "lockup", "vector", "watermark", "approvalManifest"):
        if not _valid_asset(root, assets.get(key)):
            issues.append(f"Video brand snapshot asset {key} is missing or changed.")
    timing = config.get("timing") if isinstance(config.get("timing"), dict) else {}
    opening = float(timing.get("openingSeconds", 0) or 0)
    closing = float(timing.get("closingSeconds", 0) or 0)
    if not 1.5 <= opening <= 2:
        issues.append("Opening brand ident timing must be between 1.5 and 2 seconds.")
    if not 2 <= closing <= 3:
        issues.append("Closing brand ident timing must be between 2 and 3 seconds.")
    stamp = config.get("thumbnailStamp") if isinstance(config.get("thumbnailStamp"), dict) else {}
    if stamp.get("corner") not in {"top-left", "top-right", "bottom-left", "bottom-right"}:
        issues.append("Thumbnail brand stamp corner is invalid.")
    fraction = float(stamp.get("widthFraction", 0) or 0)
    if not 0.03 <= fraction <= 0.08:
        issues.append("Thumbnail brand stamp width must stay between 3% and 8%.")
    return issues


def snapshot_brand(root: Path, *, library_path: str | Path | None = None, previous: dict[str, Any] | None = None, refresh: bool = False) -> dict[str, Any]:
    library = Path(library_path or (previous or {}).get("libraryPath") or DEFAULT_BRAND_LIBRARY).resolve(strict=False)
    approval = _manifest(library)
    revision = str(approval["revision"])
    previous = previous if isinstance(previous, dict) else {}
    if not refresh and previous.get("revision") == revision and not validate_brand_config(root, previous):
        return previous

    runtime = _manifest_path(library, str((approval.get("runtime") or {}).get("path") or ""))
    vector = _manifest_path(library, str((approval.get("vectorMaster") or {}).get("path") or ""))
    manifest_path = library / "manifest.json"
    monogram = _source_monogram(library)
    for role, source in (("lockup", runtime), ("vector", vector), ("approvalManifest", manifest_path)):
        if not source.is_file():
            raise ProjectError(f"The approved Second Pressing {role} source is missing: {source}")
    if _sha256(runtime).upper() != str((approval.get("runtime") or {}).get("sha256") or "").upper():
        raise ProjectError("The approved Second Pressing runtime lockup hash does not match its manifest.")
    if _sha256(vector).upper() != str((approval.get("vectorMaster") or {}).get("sha256") or "").upper():
        raise ProjectError("The approved Second Pressing vector hash does not match its manifest.")

    target = root / "video" / "assets" / "brand" / revision
    target.mkdir(parents=True, exist_ok=True)
    destinations = {
        "lockup": (runtime, target / "second-pressing-lockup-runtime-2400x600.png"),
        "vector": (vector, target / "second-pressing-lockup-master-outlined.svg"),
        "monogram": (monogram, target / "second-pressing-monogram-runtime-1600x1600.png"),
        "watermark": (monogram, target / "second-pressing-watermark-sp-1600x1600.png"),
        "approvalManifest": (manifest_path, target / "manifest.json"),
    }
    for source, destination in destinations.values():
        _copy_atomic(source, destination)
    assets = {key: _asset_record(root, destination, role=key, source=source) for key, (source, destination) in destinations.items()}
    return {
        "enabled": True,
        "profile": "second-pressing",
        "revision": revision,
        "libraryPath": str(library),
        "assets": assets,
        "timing": {"openingSeconds": OPENING_SECONDS, "closingSeconds": CLOSING_SECONDS},
        "thumbnailStamp": {"enabled": False, "corner": "top-left", "widthFraction": 0.045},
        "snapshot": {"revision": revision, "createdAt": utc_now(), "approvalManifestSha256": _sha256(manifest_path), "sourceRuntimeSha256": _sha256(runtime), "sourceVectorSha256": _sha256(vector), "sourceMonogramSha256": _sha256(monogram), "immutableProjectAssets": True},
    }


def brand_asset_path(root: Path, config: dict[str, Any], key: str) -> Path:
    if not isinstance(config, dict) or not bool(config.get("enabled")):
        raise ProjectError("Second Pressing branding is not enabled.")
    record = (config.get("assets") or {}).get(key)
    if not _valid_asset(root, record):
        raise ProjectError(f"Video brand snapshot asset {key} is missing or changed.")
    return (root / str(record["path"])).resolve(strict=False)


def brand_input_props(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict) or not bool(config.get("enabled")):
        return {"enabled": False}
    timing = config.get("timing") if isinstance(config.get("timing"), dict) else {}
    stamp = config.get("thumbnailStamp") if isinstance(config.get("thumbnailStamp"), dict) else {}
    return {
        "enabled": True,
        "profile": config.get("profile", "second-pressing"),
        "revision": config.get("revision"),
        "monogramKey": "brand-monogram",
        "lockupKey": "brand-lockup",
        "watermarkKey": "brand-watermark",
        "openingSeconds": float(timing.get("openingSeconds", OPENING_SECONDS)),
        "closingSeconds": float(timing.get("closingSeconds", CLOSING_SECONDS)),
        "thumbnailStamp": {"enabled": bool(stamp.get("enabled", False)), "corner": stamp.get("corner", "top-left"), "widthFraction": float(stamp.get("widthFraction", 0.045))},
    }


def brand_snapshot_assets(root: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(config, dict) or not bool(config.get("enabled")):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key in ("monogram", "lockup", "watermark", "vector", "approvalManifest"):
        path = brand_asset_path(root, config, key)
        result[f"brand-{key}"] = {"relativePath": path.relative_to(root).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}
    return result
