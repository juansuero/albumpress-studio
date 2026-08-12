from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
from pathlib import Path
from typing import Any

from .projects import ProjectError, atomic_write_json, load_manifest, normalized_path, project_mutation_lock, save_manifest, utc_now
from .brand import brand_asset_path, brand_input_props, default_brand_config, snapshot_brand, validate_brand_config
from .video_preparation import VideoPreparationError, default_preparation, merge_preparation, migrate_video_config, preparation_needs_refresh, prepare_video_config, refresh_video_preparation


VIDEO_SCHEMA_VERSION = 2
VIDEO_FPS = 30
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_COMPOSITION_ID = "AlbumLandscape"
REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ASSET_ROOT = REPO_ROOT / ".scratch" / "stem-comparison" / "video-design"
APPROVED_ARTWORK = Path(os.environ.get("ALBUMPRESS_DEFAULT_ARTWORK") or LEGACY_ASSET_ROOT / "little-songs-background-user.png")
APPROVED_DISPLAY_FONT = Path(os.environ.get("ALBUMPRESS_DEFAULT_DISPLAY_FONT") or LEGACY_ASSET_ROOT / "Bevan-Regular.ttf")
APPROVED_UTILITY_FONT = REPO_ROOT / "frontend" / "node_modules" / "@fontsource-variable" / "atkinson-hyperlegible-next" / "files" / "atkinson-hyperlegible-next-latin-wght-normal.woff2"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FINISHES = {"Off", "Subtle", "Textured"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            if struct.unpack(">I", handle.read(4))[0] != 13 or handle.read(4) != b"IHDR":
                return None
            return struct.unpack(">II", handle.read(8))
    except (OSError, struct.error):
        return None


def _relative(root: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()


def _resolve_project_asset(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ProjectError("Video asset is outside the Album Project.") from exc
    return candidate


def _asset_record(root: Path, path: Path, *, role: str, family: str) -> dict[str, Any]:
    if not path.is_file():
        raise ProjectError(f"The approved {role} asset is missing: {path}")
    dimensions = _png_dimensions(path) if role == "artwork" else None
    if role == "artwork" and not dimensions:
        raise ProjectError("The artwork must be a readable PNG.")
    if role in {"displayFont", "displayFontItalic"} and path.read_bytes()[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}:
        raise ProjectError("The approved display font is not a readable TrueType/OpenType font.")
    if role == "utilityFont" and path.read_bytes()[:4] not in {b"wOF2", b"wOFF", b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}:
        raise ProjectError("The approved utility font is not a readable web font.")
    return {
        "role": role,
        "path": _relative(root, path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "family": family,
        "mimeType": "image/png" if role == "artwork" else ("font/ttf" if role in {"displayFont", "displayFontItalic"} else "font/woff2"),
        **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
    }


def _copy_approved_asset(root: Path, source: Path, destination: Path, *, role: str, family: str) -> dict[str, Any]:
    if not source.is_file():
        raise ProjectError(f"The approved {role} source asset is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)
    return _asset_record(root, destination, role=role, family=family)


def _default_album(manifest: dict[str, Any]) -> str:
    value = str(manifest.get("albumName") or manifest.get("projectName") or "Album Project")
    return re.sub(r"^\[\d{4}\]\s*", "", value).strip() or "Album Project"


def _default_config(manifest: dict[str, Any]) -> dict[str, Any]:
    album = _default_album(manifest)
    artist = "Colter Wall" if album.casefold() == "little songs" else ""
    return {
        "schemaVersion": VIDEO_SCHEMA_VERSION,
        "compositionId": VIDEO_COMPOSITION_ID,
        "width": VIDEO_WIDTH,
        "height": VIDEO_HEIGHT,
        "fps": VIDEO_FPS,
        "artist": artist,
        "album": album,
        "typography": {
            "displayFontFamily": "Bevan",
            "utilityFontFamily": "Atkinson Hyperlegible Next",
        },
        "colors": {
            "primary": "#B74633",
            "secondary": "#F7F3EA",
            "accent": "#B74633",
            "marker": "#D99A59",
            "scrim": "#22201F",
        },
        "cinematicFinish": "Subtle",
        "reducedMotion": False,
        "descriptionNotes": "",
        "brand": default_brand_config(),
        "assets": {
            "artwork": {"path": "video/assets/little-songs-background-user.png"},
            "displayFont": {"path": "video/assets/Bevan-Regular.ttf"},
            "utilityFont": {"path": "video/assets/atkinson-hyperlegible-next-latin-wght-normal.woff2"},
        },
        "tracks": [],
        "preparation": default_preparation(),
        "provenance": {},
    }


def _contrast_ratio(first: str, second: str) -> float:
    def channel(value: int) -> float:
        normalized = value / 255
        return normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4

    def luminance(value: str) -> float:
        red, green, blue = (int(value[index:index + 2], 16) for index in (1, 3, 5))
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _final_path(root: Path, output: dict[str, Any]) -> tuple[Path, str]:
    output_name = Path(str(output.get("path", ""))).name
    slot = str(output.get("slot", "A"))
    prefix = f"{slot}_"
    final_name = output_name[len(prefix):] if output_name.startswith(prefix) else output_name
    relative = Path("final") / final_name
    return _resolve_project_asset(root, relative), relative.as_posix()


def _current_track_snapshot(manifest: dict[str, Any], root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    selections = manifest.get("selections") if isinstance(manifest.get("selections"), dict) else {}
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    tracks: list[dict[str, Any]] = []
    issues: list[str] = []
    ordered = sorted((item for item in manifest.get("tracks", []) if isinstance(item, dict)), key=lambda item: int(item.get("sequence", 0)))
    for track in ordered:
        track_id = str(track.get("trackId", ""))
        selection = selections.get(track_id)
        output = outputs.get(selection.get("outputId")) if isinstance(selection, dict) else None
        if not isinstance(selection, dict) or not isinstance(output, dict):
            issues.append(f"Track {track.get('sequence', '?')} has no registered Selection.")
            continue
        if output.get("trackId") != track_id or output.get("status") != "valid" or output.get("semanticStatus") != "confirmed":
            issues.append(f"Track {track.get('sequence', '?')} does not have a valid semantically confirmed Final Instrumental.")
            continue
        final_path, final_relative = _final_path(root, output)
        if not final_path.is_file() or final_path.stat().st_size <= 0:
            issues.append(f"Track {track.get('sequence', '?')} Final Instrumental is missing from final/.")
            continue
        duration = float(output.get("durationSeconds") or track.get("durationSeconds") or 0)
        if duration <= 0:
            issues.append(f"Track {track.get('sequence', '?')} has no usable Final Instrumental duration.")
            continue
        if abs(float(track.get("durationSeconds") or 0) - duration) > max(0.25, float(track.get("durationSeconds") or 0) * 0.05):
            issues.append(f"Track {track.get('sequence', '?')} source and Final Instrumental durations differ materially.")
        tracks.append({
            "trackId": track_id,
            "sequence": int(track.get("sequence", len(tracks) + 1)),
            "title": str(track.get("title") or f"Track {len(tracks) + 1}"),
            "durationSeconds": duration,
            "outputId": str(selection.get("outputId")),
            "slot": str(selection.get("slot") or output.get("slot") or "A"),
            "fileFingerprint": output.get("fileFingerprint"),
            "finalPath": final_relative,
        })
    if len(tracks) != len(ordered):
        issues.append(f"Expected {len(ordered)} current Tracks, found {len(tracks)} usable Final Instrumentals.")
    return tracks, issues


def _timeline(tracks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    cursor = 0
    cursor_seconds = 0.0
    timeline: list[dict[str, Any]] = []
    for track in tracks:
        next_frame = int((cursor_seconds + float(track["durationSeconds"])) * VIDEO_FPS + 0.5)
        frames = max(1, next_frame - cursor)
        timeline.append({**track, "startFrame": cursor, "durationInFrames": frames})
        cursor += frames
        cursor_seconds += float(track["durationSeconds"])
    return timeline, cursor


def _validate_config(root: Path, config: dict[str, Any], current_tracks: list[dict[str, Any]], current_issues: list[str]) -> list[str]:
    issues = list(current_issues)
    if config.get("schemaVersion") != VIDEO_SCHEMA_VERSION:
        if config.get("schemaVersion") == 1:
            issues.append("Video configuration v1 requires an explicit Refresh preparation migration.")
        else:
            issues.append("Video configuration version is unsupported.")
    if config.get("compositionId") != VIDEO_COMPOSITION_ID:
        issues.append("Video configuration does not target Album Landscape.")
    colors = config.get("colors") if isinstance(config.get("colors"), dict) else {}
    for key in ("primary", "secondary", "accent", "marker", "scrim"):
        if not HEX_RE.fullmatch(str(colors.get(key, ""))):
            issues.append(f"Video color {key} is invalid.")
    if all(HEX_RE.fullmatch(str(colors.get(key, ""))) for key in ("primary", "secondary", "accent", "marker", "scrim")):
        for key in ("primary", "secondary"):
            if _contrast_ratio(str(colors[key]), str(colors["scrim"])) < 3:
                issues.append(f"Video color {key} has insufficient contrast against the scrim.")
    if config.get("cinematicFinish") not in FINISHES:
        issues.append("Cinematic Finish must be Off, Subtle, or Textured.")
    assets = config.get("assets") if isinstance(config.get("assets"), dict) else {}
    asset_keys = ["artwork", "displayFont", "utilityFont"]
    if str((config.get("typography") or {}).get("displayFontFamily", "")) == "Besley":
        asset_keys.append("displayFontItalic")
    for key in asset_keys:
        record = assets.get(key)
        if not isinstance(record, dict) or not record.get("path"):
            issues.append(f"Video {key} asset is not registered.")
            continue
        try:
            path = _resolve_project_asset(root, str(record["path"]))
        except ProjectError as exc:
            issues.append(str(exc))
            continue
        if not path.is_file():
            issues.append(f"Video {key} asset is missing from the Project Folder.")
            continue
        if record.get("sha256") and record["sha256"] != _sha256(path):
            issues.append(f"Video {key} asset fingerprint changed.")
        if key == "artwork" and not _png_dimensions(path):
            issues.append("Video artwork is not a readable PNG.")
    issues.extend(validate_brand_config(root, config.get("brand")))
    saved_tracks = config.get("tracks") if isinstance(config.get("tracks"), list) else []
    if len(saved_tracks) != len(current_tracks):
        issues.append("Video configuration Track set is stale.")
    for saved, current in zip(saved_tracks, current_tracks):
        for key in ("trackId", "title", "outputId", "fileFingerprint", "finalPath"):
            if saved.get(key) != current.get(key):
                issues.append(f"Video configuration is stale for Track {current.get('sequence', '?')}.")
                break
    return list(dict.fromkeys(issues))


def _has_fingerprint_drift(config: dict[str, Any], current_tracks: list[dict[str, Any]]) -> bool:
    saved = {str(item.get("trackId")): item for item in config.get("tracks", []) if isinstance(item, dict)}
    for current in current_tracks:
        previous = saved.get(str(current.get("trackId")))
        if not previous:
            continue
        same_output = all(previous.get(key) == current.get(key) for key in ("outputId", "finalPath"))
        if same_output and previous.get("fileFingerprint") != current.get("fileFingerprint"):
            return True
    return False


def build_video_state(manifest_path: str | Path, *, _skip_prepare: bool = False) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    root = path.parent
    manifest = load_manifest(path)
    defaults = _default_config(manifest)
    config_path = root / "video" / "config.json"
    config: dict[str, Any] = defaults
    config_exists = config_path.is_file()
    issues: list[str] = []
    if config_exists:
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw_colors = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
                raw_brand = raw.get("brand") if isinstance(raw.get("brand"), dict) else {}
                config = {**defaults, **raw, "colors": {**defaults["colors"], **raw_colors}, "brand": {**defaults["brand"], **raw_brand}}
            else:
                issues.append("Video configuration is not a JSON object.")
        except (OSError, json.JSONDecodeError):
            issues.append("Video configuration could not be read.")
    else:
        issues.append("Configure the approved artwork and local fonts before previewing.")
    current_tracks, current_issues = _current_track_snapshot(manifest, root)
    if config_exists and config.get("schemaVersion") == VIDEO_SCHEMA_VERSION and not current_issues and not _skip_prepare:
        try:
            if not _has_fingerprint_drift(config, current_tracks) and preparation_needs_refresh(root, config, current_tracks):
                config = prepare_video_config(root, config, current_tracks)
                atomic_write_json(config_path, config)
        except VideoPreparationError as exc:
            issues.append(str(exc))
        except (OSError, ValueError, TypeError) as exc:
            issues.append(f"Video preparation could not be persisted: {exc}")
    if config_exists and config.get("schemaVersion") == VIDEO_SCHEMA_VERSION and not _skip_prepare:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            issues.append("Prepared Video configuration could not be reloaded.")
    if config_exists:
        issues.extend(_validate_config(root, config, current_tracks, current_issues))
    else:
        issues.extend(current_issues)
    timeline, duration = _timeline(config.get("tracks", []) if config_exists else current_tracks)
    config_tracks = timeline if config_exists else []
    artwork = (config.get("assets") or {}).get("artwork", {}) if isinstance(config.get("assets"), dict) else {}
    display_font = (config.get("assets") or {}).get("displayFont", {}) if isinstance(config.get("assets"), dict) else {}
    display_font_italic = (config.get("assets") or {}).get("displayFontItalic", {}) if isinstance(config.get("assets"), dict) else {}
    utility_font = (config.get("assets") or {}).get("utilityFont", {}) if isinstance(config.get("assets"), dict) else {}
    input_tracks = [{**item, "audioUrl": f"/api/video/audio/{item['trackId']}"} for item in config_tracks]
    brand_config = config.get("brand") if isinstance(config.get("brand"), dict) else default_brand_config()
    brand_props = brand_input_props(brand_config)
    if brand_props.get("enabled"):
        brand_props.update({
            "monogramUrl": "/api/video/assets/brand-monogram",
            "lockupUrl": "/api/video/assets/brand-lockup",
            "watermarkUrl": "/api/video/assets/brand-watermark",
        })
    input_props = {
        "artist": str(config.get("artist", "")),
        "album": str(config.get("album", "")),
        "artworkUrl": "/api/video/assets/artwork",
        "displayFontUrl": "/api/video/assets/display-font",
        "displayFontItalicUrl": "/api/video/assets/display-font-italic" if display_font_italic.get("path") else "/api/video/assets/display-font",
        "utilityFontUrl": "/api/video/assets/utility-font",
        "displayFontFamily": str((config.get("typography") or {}).get("displayFontFamily", "Bevan")),
        "utilityFontFamily": str((config.get("typography") or {}).get("utilityFontFamily", "Atkinson Hyperlegible Next")),
        "colors": config.get("colors", {}),
        "cinematicFinish": config.get("cinematicFinish", "Textured"),
        "reducedMotion": bool(config.get("reducedMotion", False)),
        "descriptionNotes": str(config.get("descriptionNotes", "")),
        "tracks": input_tracks,
        "includeAudio": True,
        "textureUrl": "/api/video/assets/texture",
        "fadeInSeconds": float(((config.get("preparation") or {}).get("settings") or {}).get("audioFadeInSeconds", 1.0)),
        "fadeOutSeconds": float(((config.get("preparation") or {}).get("settings") or {}).get("audioFadeOutSeconds", 2.0)),
        "brand": brand_props,
    }
    state_assets = {"artwork": artwork, "displayFont": display_font, "utilityFont": utility_font}
    if display_font_italic:
        state_assets["displayFontItalic"] = display_font_italic
    if brand_props.get("enabled"):
        for key, record in (brand_config.get("assets") or {}).items():
            if key in {"monogram", "lockup", "watermark", "vector", "approvalManifest"}:
                state_assets[f"brand-{key}"] = record
    return {
        "status": "ready" if not issues else "blocked",
        "ready": not issues,
        "issues": list(dict.fromkeys(issues)),
        "projectFolder": str(root),
        "configPath": str(config_path),
        "configRelativePath": _relative(root, config_path),
        "config": config,
        "assets": state_assets,
        "composition": {
            "id": VIDEO_COMPOSITION_ID,
            "width": VIDEO_WIDTH,
            "height": VIDEO_HEIGHT,
            "fps": VIDEO_FPS,
            "durationInFrames": duration,
            "durationSeconds": round(duration / VIDEO_FPS, 3),
            "timeline": timeline,
            "inputProps": input_props,
        },
        "provenance": config.get("provenance", {}),
        "preparation": config.get("preparation", default_preparation()),
    }


def configure_video(manifest_path: str | Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    path = normalized_path(manifest_path)
    root = path.parent
    payload = payload or {}
    manifest = load_manifest(path)
    base = _default_config(manifest)
    config_path = root / "video" / "config.json"
    existing: dict[str, Any] = {}
    if config_path.is_file():
        try:
            value = json.loads(config_path.read_text(encoding="utf-8"))
            existing = migrate_video_config(manifest, value) if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing_brand = existing.get("brand") if isinstance(existing.get("brand"), dict) else {}
    base = {**base, **existing, "colors": {**base["colors"], **(existing.get("colors") or {})}, "brand": {**base["brand"], **existing_brand}, "preparation": default_preparation() if not existing else existing.get("preparation")}
    artist = str(payload.get("artist", base["artist"])).strip()[:120]
    album = str(payload.get("album", base["album"])).strip()[:120]
    if not artist or not album:
        raise ProjectError("Artist and album text are required for Album Landscape.")
    colors = payload.get("colors") if isinstance(payload.get("colors"), dict) else base["colors"]
    normalized_colors = {key: str(colors.get(key, base["colors"][key])).upper() for key in base["colors"]}
    if any(not HEX_RE.fullmatch(value) for value in normalized_colors.values()):
        raise ProjectError("Colors must be six-digit hexadecimal values.")
    finish = str(payload.get("cinematicFinish", base["cinematicFinish"]))
    if finish not in FINISHES:
        raise ProjectError("Cinematic Finish must be Off, Subtle, or Textured.")
    reduced_motion = bool(payload.get("reducedMotion", base["reducedMotion"]))
    typography_payload = payload.get("typography") if isinstance(payload.get("typography"), dict) else {}
    display_family = str(typography_payload.get("displayFontFamily", base["typography"]["displayFontFamily"]))
    utility_family = str(typography_payload.get("utilityFontFamily", base["typography"]["utilityFontFamily"]))
    if display_family not in {"Bevan", "Besley"} or utility_family not in {"Atkinson Hyperlegible Next"}:
        raise ProjectError("Choose a registered display and utility font role.")
    with project_mutation_lock(root):
        asset_dir = root / "video" / "assets"
        requested_artwork = str(payload.get("artworkPath") or "").strip()
        artwork_source_path = str(payload.get("artworkSourcePath") or "").strip()
        existing_artwork = (existing.get("assets") or {}).get("artwork") if isinstance(existing.get("assets"), dict) else None
        if artwork_source_path:
            artwork_asset = _copy_approved_asset(root, normalized_path(artwork_source_path), asset_dir / "western-swing-cattle-background-more-sky-2560x1440.png", role="artwork", family="Artwork")
        elif requested_artwork:
            artwork_source = _resolve_project_asset(root, requested_artwork)
            artwork_asset = _asset_record(root, artwork_source, role="artwork", family="Artwork")
        elif isinstance(existing_artwork, dict) and existing_artwork.get("path") and _resolve_project_asset(root, str(existing_artwork["path"])).is_file():
            artwork_source = _resolve_project_asset(root, str(existing_artwork["path"]))
            artwork_asset = _asset_record(root, artwork_source, role="artwork", family="Artwork")
        else:
            if not APPROVED_ARTWORK.is_file():
                raise ProjectError("Provide artworkSourcePath or set ALBUMPRESS_DEFAULT_ARTWORK to a local PNG before configuring video.")
            artwork_asset = _copy_approved_asset(root, APPROVED_ARTWORK, asset_dir / "little-songs-background-user.png", role="artwork", family="Artwork")
        assets = {
            "artwork": artwork_asset,
            "utilityFont": _copy_approved_asset(root, APPROVED_UTILITY_FONT, asset_dir / "atkinson-hyperlegible-next-latin-wght-normal.woff2", role="utilityFont", family="Atkinson Hyperlegible Next"),
        }
        existing_assets = existing.get("assets") if isinstance(existing.get("assets"), dict) else {}
        if display_family == "Besley":
            requested_display = str(payload.get("displayFontPath") or "").strip()
            requested_display_italic = str(payload.get("displayFontItalicPath") or "").strip()
            existing_display = existing_assets.get("displayFont") if isinstance(existing_assets.get("displayFont"), dict) else None
            existing_display_italic = existing_assets.get("displayFontItalic") if isinstance(existing_assets.get("displayFontItalic"), dict) else None
            if requested_display:
                display_asset = _copy_approved_asset(root, normalized_path(requested_display), asset_dir / "Besley-VariableFont_wght.ttf", role="displayFont", family="Besley")
            elif isinstance(existing_display, dict) and existing_display.get("path") and _resolve_project_asset(root, str(existing_display["path"])).is_file():
                display_asset = _asset_record(root, _resolve_project_asset(root, str(existing_display["path"])), role="displayFont", family="Besley")
            else:
                raise ProjectError("Besley requires a local regular TTF/OTF source path for the project snapshot.")
            if requested_display_italic:
                display_italic_asset = _copy_approved_asset(root, normalized_path(requested_display_italic), asset_dir / "Besley-Italic-VariableFont_wght.ttf", role="displayFontItalic", family="Besley")
            elif isinstance(existing_display_italic, dict) and existing_display_italic.get("path") and _resolve_project_asset(root, str(existing_display_italic["path"])).is_file():
                display_italic_asset = _asset_record(root, _resolve_project_asset(root, str(existing_display_italic["path"])), role="displayFontItalic", family="Besley")
            else:
                raise ProjectError("Besley requires a local italic TTF/OTF source path for the project snapshot.")
            assets.update({"displayFont": display_asset, "displayFontItalic": display_italic_asset})
        else:
            requested_display = str(payload.get("displayFontPath") or "").strip()
            existing_display = existing_assets.get("displayFont") if isinstance(existing_assets.get("displayFont"), dict) else None
            if requested_display:
                display_asset = _copy_approved_asset(root, normalized_path(requested_display), asset_dir / "Bevan-Regular.ttf", role="displayFont", family="Bevan")
            elif isinstance(existing_display, dict) and existing_display.get("path") and _resolve_project_asset(root, str(existing_display["path"])).is_file():
                display_asset = _asset_record(root, _resolve_project_asset(root, str(existing_display["path"])), role="displayFont", family="Bevan")
            elif APPROVED_DISPLAY_FONT.is_file():
                display_asset = _copy_approved_asset(root, APPROVED_DISPLAY_FONT, asset_dir / "Bevan-Regular.ttf", role="displayFont", family="Bevan")
            else:
                raise ProjectError("Provide displayFontPath or set ALBUMPRESS_DEFAULT_DISPLAY_FONT to a local TTF/OTF before configuring video.")
            assets["displayFont"] = display_asset
        current_tracks, _ = _current_track_snapshot(manifest, root)
        brand_payload = payload.get("brand") if isinstance(payload.get("brand"), dict) else {}
        brand_enabled = bool(brand_payload.get("enabled", (base.get("brand") or {}).get("enabled", False)))
        if brand_enabled:
            brand = snapshot_brand(
                root,
                library_path=brand_payload.get("libraryPath") or (base.get("brand") or {}).get("libraryPath"),
                previous=base.get("brand") if isinstance(base.get("brand"), dict) else None,
                refresh=bool(brand_payload.get("refresh", False)),
            )
            thumbnail_stamp = brand_payload.get("thumbnailStamp") if isinstance(brand_payload.get("thumbnailStamp"), dict) else {}
            if thumbnail_stamp:
                brand["thumbnailStamp"] = {**brand["thumbnailStamp"], **thumbnail_stamp}
        else:
            brand = {**default_brand_config(), "enabled": False, "libraryPath": (base.get("brand") or {}).get("libraryPath", str(default_brand_config()["libraryPath"]))}
        preparation = merge_preparation(base.get("preparation"))
        payload_preparation = payload.get("preparation") if isinstance(payload.get("preparation"), dict) else {}
        if payload_preparation.get("artworkMode") in {"Auto", "Original"}:
            preparation["artworkMode"] = payload_preparation["artworkMode"]
        if isinstance(payload_preparation.get("trackOverrides"), dict):
            preparation["trackOverrides"] = payload_preparation["trackOverrides"]
        if isinstance(payload_preparation.get("settings"), dict):
            preparation["settings"].update(payload_preparation["settings"])
        config = {
            **base,
            "schemaVersion": VIDEO_SCHEMA_VERSION,
            "artist": artist,
            "album": album,
            "typography": {"displayFontFamily": display_family, "utilityFontFamily": utility_family},
            "colors": normalized_colors,
            "cinematicFinish": finish,
            "reducedMotion": reduced_motion,
            "descriptionNotes": str(payload.get("descriptionNotes", "")).strip()[:1000],
            "brand": brand,
            "assets": assets,
            "tracks": current_tracks,
            "preparation": preparation,
            "provenance": {
                "source": "current validated Final Instrumentals only",
                "projectManifest": "project.json",
                "selectionSnapshot": [{"trackId": item["trackId"], "outputId": item["outputId"], "fileFingerprint": item.get("fileFingerprint")} for item in current_tracks],
                "assetSource": "user-approved local artwork/font packet",
                "configuredAt": utc_now(),
            },
        }
        config = prepare_video_config(root, config, current_tracks)
        atomic_write_json(config_path, config)
        manifest["video"] = {"status": "configured", "configPath": "video/config.json", "schemaVersion": VIDEO_SCHEMA_VERSION, "updatedAt": utc_now(), "preparationUpdatedAt": config.get("preparation", {}).get("updatedAt")}
        save_manifest(manifest, root)
    return build_video_state(path, _skip_prepare=True)


def video_asset_path(manifest_path: str | Path, kind: str) -> Path:
    if kind.startswith("brand-"):
        state = build_video_state(manifest_path)
        return brand_asset_path(Path(state["projectFolder"]), state["config"].get("brand"), kind.removeprefix("brand-"))
    if kind not in {"artwork", "texture", "display-font", "display-font-italic", "utility-font"}:
        raise ProjectError("Unknown video asset.")
    state = build_video_state(manifest_path)
    config_key = {"artwork": "effectiveArtwork", "texture": "texture", "display-font": "displayFont", "display-font-italic": "displayFontItalic", "utility-font": "utilityFont"}[kind]
    if config_key not in state["config"].get("assets", {}):
        config_key = "artwork" if kind == "artwork" else ("displayFont" if kind == "display-font-italic" else config_key)
    record = state["config"].get("assets", {}).get(config_key)
    if not isinstance(record, dict) or not record.get("path"):
        raise ProjectError("Video asset is not registered.")
    path = _resolve_project_asset(Path(state["projectFolder"]), str(record["path"]))
    if not path.is_file():
        raise ProjectError("Video asset is no longer available.")
    return path


def video_audio_path(manifest_path: str | Path, track_id: str) -> Path:
    state = build_video_state(manifest_path)
    if not state["ready"]:
        raise ProjectError("Video preview is blocked until the current Final Instrumentals are configured and valid.")
    track = next((item for item in state["composition"]["timeline"] if item.get("trackId") == track_id), None)
    if not isinstance(track, dict) or not track.get("finalPath"):
        raise ProjectError("The Track is not in the configured Video Package.")
    path = _resolve_project_asset(Path(state["projectFolder"]), str(track["finalPath"]))
    if not path.is_file():
        raise ProjectError("The configured Final Instrumental is no longer available.")
    return path
