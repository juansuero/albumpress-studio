from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import subprocess
import zlib
from array import array
from pathlib import Path
from typing import Any, Callable

from .projects import ProjectError, atomic_write_json, utc_now


PREPARATION_SCHEMA_VERSION = 2
ARTWORK_RECIPE_VERSION = "lanczos-unsharp-r1"
SILENCE_ANALYZER_VERSION = "pcm-window-hysteresis-r2"
TEXTURE_RECIPE_VERSION = "raster-grain-r1"
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
DERIVED_ARTWORK_WIDTH = 3840
DERIVED_ARTWORK_HEIGHT = 2160
MIN_SILENCE_BLOCK_SECONDS = 0.5
ANALYSIS_WINDOW_SECONDS = 0.25
ANALYSIS_SAMPLE_RATE = 8000
ENTER_THRESHOLD_DB = -45.0
EXIT_THRESHOLD_DB = -60.0
RELEASE_HOLD_SECONDS = 0.75
MINIMUM_ACTIVE_SECONDS = 0.5
RETAINED_TAIL_SECONDS = 1.0
ADAPTIVE_NOISE_DB = -35.0
CONSERVATIVE_NOISE_DB = -60.0
REVIEW_DISAGREEMENT_SECONDS = 4.0
MINIMUM_TRIM_SECONDS = 0.75
MAX_PROPOSED_REMOVAL_SECONDS = 20.0
FADE_IN_SECONDS = 1.0
FADE_OUT_SECONDS = 2.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class VideoPreparationError(ProjectError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            if handle.read(8) != PNG_SIGNATURE:
                return None
            if struct.unpack(">I", handle.read(4))[0] != 13 or handle.read(4) != b"IHDR":
                return None
            return struct.unpack(">II", handle.read(8))
    except (OSError, struct.error):
        return None


def _inside(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise VideoPreparationError("Video preparation asset is outside the Project Folder.") from exc
    return candidate


def _asset_record(root: Path, path: Path, *, role: str, source: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise VideoPreparationError(f"Prepared {role} asset is missing: {path}")
    dimensions = png_dimensions(path) if path.suffix.casefold() == ".png" else None
    return {
        "role": role,
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "mimeType": "image/png" if path.suffix.casefold() == ".png" else "application/octet-stream",
        **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
        **({"source": source} if source else {}),
    }


def default_preparation() -> dict[str, Any]:
    return {
        "schemaVersion": PREPARATION_SCHEMA_VERSION,
        "artworkMode": "Auto",
        "settings": {
            "minimumSilenceSeconds": MIN_SILENCE_BLOCK_SECONDS,
            "retainedTailSeconds": RETAINED_TAIL_SECONDS,
            "adaptiveNoiseDb": ADAPTIVE_NOISE_DB,
            "conservativeNoiseDb": CONSERVATIVE_NOISE_DB,
            "reviewDisagreementSeconds": REVIEW_DISAGREEMENT_SECONDS,
            "maxProposedRemovalSeconds": MAX_PROPOSED_REMOVAL_SECONDS,
            "windowSeconds": ANALYSIS_WINDOW_SECONDS,
            "analysisSampleRate": ANALYSIS_SAMPLE_RATE,
            "enterThresholdDb": ENTER_THRESHOLD_DB,
            "exitThresholdDb": EXIT_THRESHOLD_DB,
            "releaseHoldSeconds": RELEASE_HOLD_SECONDS,
            "minimumActiveSeconds": MINIMUM_ACTIVE_SECONDS,
            "tailPaddingSeconds": RETAINED_TAIL_SECONDS,
            "minimumTrimSeconds": MINIMUM_TRIM_SECONDS,
            "audioFadeInSeconds": FADE_IN_SECONDS,
            "audioFadeOutSeconds": FADE_OUT_SECONDS,
        },
        "trackOverrides": {},
        "artwork": {},
        "texture": {},
        "analysisCache": {},
        "summary": {"tracksAnalyzed": 0, "secondsRemoved": 0.0, "reviewCount": 0},
        "status": "pending",
        "updatedAt": None,
    }


def merge_preparation(value: Any) -> dict[str, Any]:
    base = default_preparation()
    if not isinstance(value, dict):
        return base
    settings = value.get("settings") if isinstance(value.get("settings"), dict) else {}
    base["settings"].update(settings)
    for key in ("artworkMode", "trackOverrides", "artwork", "texture", "analysisCache", "summary", "status", "settingsFingerprint", "updatedAt", "migration"):
        if key in value:
            base[key] = value[key]
    base["schemaVersion"] = PREPARATION_SCHEMA_VERSION
    return base


def _write_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    payload = PNG_SIGNATURE
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(raw, 9))
    payload += chunk(b"IEND", b"")
    path.write_bytes(payload)


def ensure_raster_texture(root: Path) -> dict[str, Any]:
    derived = root / "video" / "assets" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    path = derived / f"texture-{TEXTURE_RECIPE_VERSION}.png"
    if not path.is_file() or png_dimensions(path) != (256, 256):
        rows: list[bytes] = []
        for y in range(256):
            row = bytearray()
            for x in range(256):
                bright = ((x * 37 + y * 17 + (x ^ y) * 3) % 113) < 7
                dark = ((x * 11 + y * 29) % 149) < 5
                if bright:
                    row.extend((255, 255, 255, 34))
                elif dark:
                    row.extend((0, 0, 0, 24))
                else:
                    row.extend((255, 255, 255, 255, 0))
            rows.append(bytes(row))
        temporary = path.with_suffix(".tmp")
        _write_png(temporary, 256, 256, rows)
        temporary.replace(path)
    return _asset_record(root, path, role="rasterTexture", source=TEXTURE_RECIPE_VERSION)


def _run_ffmpeg_resize(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(".tmp.png")
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", "scale=3840:2160:flags=lanczos,unsharp=5:5:0.35:5:5:0",
        "-frames:v", "1", "-c:v", "png", "-compression_level", "9", "-pred", "mixed", str(temporary),
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise VideoPreparationError("FFmpeg is required for deterministic artwork preparation.") from exc
    if result.returncode != 0 or png_dimensions(temporary) != (DERIVED_ARTWORK_WIDTH, DERIVED_ARTWORK_HEIGHT):
        temporary.unlink(missing_ok=True)
        detail = (result.stderr or "FFmpeg did not produce a 3840×2160 PNG.").strip()
        raise VideoPreparationError(detail[-1000:])
    temporary.replace(destination)


def prepare_artwork(root: Path, artwork_record: dict[str, Any], mode: str = "Auto") -> dict[str, Any]:
    relative = str(artwork_record.get("path") or "")
    source = _inside(root, relative)
    dimensions = png_dimensions(source)
    if dimensions is None:
        raise VideoPreparationError("Uploaded artwork must be a readable PNG.")
    source_hash = sha256(source)
    insufficient = dimensions[0] < VIDEO_WIDTH or dimensions[1] < VIDEO_HEIGHT
    mode = mode if mode in {"Auto", "Original"} else "Auto"
    if mode == "Original" or not insufficient:
        effective = _asset_record(root, source, role="effectiveArtwork", source="original")
        return {
            "mode": mode,
            "source": _asset_record(root, source, role="artwork", source="uploaded artwork"),
            "effective": effective,
            "derived": False,
            "cacheKey": None,
            "sourceSha256": source_hash,
            "sourceDimensions": {"width": dimensions[0], "height": dimensions[1]},
            "recipeVersion": ARTWORK_RECIPE_VERSION,
            "parameters": {"targetWidth": DERIVED_ARTWORK_WIDTH, "targetHeight": DERIVED_ARTWORK_HEIGHT, "filter": "lanczos+unsharp(0.35)"},
        }
    cache_key = f"{source_hash}:{dimensions[0]}x{dimensions[1]}:{ARTWORK_RECIPE_VERSION}:3840x2160:lanczos-unsharp-0.35"
    derived = root / "video" / "assets" / "derived" / f"artwork-{source_hash[:16]}-{ARTWORK_RECIPE_VERSION}-3840x2160.png"
    if not derived.is_file() or png_dimensions(derived) != (DERIVED_ARTWORK_WIDTH, DERIVED_ARTWORK_HEIGHT):
        derived.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg_resize(source, derived)
        cache_hit = False
    else:
        cache_hit = True
    return {
        "mode": mode,
        "source": _asset_record(root, source, role="artwork", source="uploaded artwork"),
        "effective": _asset_record(root, derived, role="effectiveArtwork", source=ARTWORK_RECIPE_VERSION),
        "derived": True,
        "cacheKey": cache_key,
        "cacheHit": cache_hit,
        "sourceSha256": source_hash,
        "sourceDimensions": {"width": dimensions[0], "height": dimensions[1]},
        "recipeVersion": ARTWORK_RECIPE_VERSION,
        "parameters": {"targetWidth": DERIVED_ARTWORK_WIDTH, "targetHeight": DERIVED_ARTWORK_HEIGHT, "filter": "lanczos+unsharp(0.35)"},
    }


def _probe_duration(path: Path) -> float:
    args = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise VideoPreparationError("FFprobe is required for safe Final Instrumental duration analysis.") from exc
    if result.returncode != 0:
        raise VideoPreparationError((result.stderr or "FFprobe could not read the Final Instrumental.").strip()[-1000:])
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise VideoPreparationError("FFprobe returned no usable duration.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise VideoPreparationError("Final Instrumental duration is not usable.")
    return duration


def _silence_pass(path: Path, noise_db: float, minimum_seconds: float) -> dict[str, Any]:
    filter_value = f"volumedetect,silencedetect=noise={noise_db:g}dB:d={minimum_seconds:g}"
    args = ["ffmpeg", "-hide_banner", "-i", str(path), "-af", filter_value, "-f", "null", os.devnull]
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise VideoPreparationError("FFmpeg is required for safe trailing-silence analysis.") from exc
    if result.returncode != 0:
        raise VideoPreparationError((result.stderr or "FFmpeg silence analysis failed.").strip()[-1000:])
    output = result.stderr or ""
    starts = [float(item) for item in re.findall(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", output)]
    ends = [float(item) for item in re.findall(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)", output)]
    max_volume = re.findall(r"max_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB", output)
    return {"noiseDb": noise_db, "minimumSeconds": minimum_seconds, "silenceStarts": starts, "silenceEnds": ends, "maxVolumeDb": float(max_volume[-1]) if max_volume else None, "rawEvidence": output[-4000:]}


def _tail_from_pass(result: dict[str, Any], duration: float) -> tuple[float, bool]:
    starts = result.get("silenceStarts") if isinstance(result.get("silenceStarts"), list) else []
    ends = result.get("silenceEnds") if isinstance(result.get("silenceEnds"), list) else []
    if not starts or not ends:
        return 0.0, False
    start = float(starts[-1])
    end = float(ends[-1])
    if end < start or abs(end - duration) > 0.25:
        return 0.0, False
    return max(0.0, duration - start), True


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))


def _decode_window_levels(path: Path, *, window_seconds: float, sample_rate: int) -> list[dict[str, float]]:
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
    ]
    try:
        result = subprocess.run(args, capture_output=True, check=False)
    except OSError as exc:
        raise VideoPreparationError("FFmpeg is required for PCM window analysis.") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace") if result.stderr else "FFmpeg PCM window analysis failed."
        raise VideoPreparationError(detail.strip()[-1000:])
    samples = array("h")
    samples.frombytes(result.stdout)
    if struct.pack("=h", 1) != struct.pack("<h", 1):
        samples.byteswap()
    window_samples = max(1, round(window_seconds * sample_rate))
    levels: list[dict[str, float]] = []
    for offset in range(0, len(samples), window_samples):
        window = samples[offset:offset + window_samples]
        if not window:
            continue
        mean_square = sum(sample * sample for sample in window) / len(window)
        peak = max(abs(sample) for sample in window) / 32768.0
        levels.append({
            "startSeconds": offset / sample_rate,
            "endSeconds": min((offset + len(window)) / sample_rate, offset / sample_rate + window_seconds),
            "rmsDb": _dbfs(math.sqrt(mean_square) / 32768.0),
            "peakDb": _dbfs(peak),
        })
    if not levels:
        raise VideoPreparationError("FFmpeg returned no PCM samples for window analysis.")
    return levels


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return -120.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[position]


def _windowed_analysis(
    levels: list[dict[str, float]],
    duration: float,
    *,
    window_seconds: float,
    sample_rate: int,
    enter_threshold_db: float,
    exit_threshold_db: float,
    release_hold_seconds: float,
    minimum_active_seconds: float,
    tail_padding_seconds: float,
    minimum_trim_seconds: float,
    max_proposed_removal_seconds: float,
    analyzer_version: str,
) -> dict[str, Any]:
    tail_window_count = max(8, round(20 / window_seconds))
    tail_start = max(0, len(levels) - tail_window_count)
    tail_floor_values = [float(item["rmsDb"]) for item in levels[tail_start:]]
    noise_floor_db = _percentile(tail_floor_values, 0.1)
    enter_windows = max(2, math.ceil(minimum_active_seconds / window_seconds))
    release_windows = max(2, math.ceil(release_hold_seconds / window_seconds))
    active = False
    pending_entry = 0
    quiet_windows = 0
    last_activity_end = 0.0
    ignored_isolated_events = 0
    for item in levels:
        rms_db = float(item["rmsDb"])
        if not active:
            if rms_db >= enter_threshold_db:
                pending_entry += 1
                if pending_entry >= enter_windows:
                    active = True
                    quiet_windows = 0
                    last_activity_end = float(item["endSeconds"])
                    pending_entry = 0
            else:
                if pending_entry:
                    ignored_isolated_events += 1
                pending_entry = 0
            continue
        if rms_db >= exit_threshold_db:
            quiet_windows = 0
            last_activity_end = float(item["endSeconds"])
        else:
            quiet_windows += 1
            if quiet_windows >= release_windows:
                active = False
                pending_entry = 0
                last_activity_end = max(last_activity_end, float(item["startSeconds"]) - (quiet_windows - 1) * window_seconds)
                quiet_windows = 0
    if active:
        last_activity_end = max(last_activity_end, float(levels[-1]["endSeconds"]))
    last_activity_end = min(duration, max(0.0, last_activity_end))
    detected_tail = max(0.0, duration - last_activity_end)
    proposed_effective = min(duration, last_activity_end + tail_padding_seconds)
    raw_removal = max(0.0, duration - proposed_effective)
    status = "confident"
    reason: str | None = None
    if last_activity_end <= 0 or not math.isfinite(last_activity_end):
        status, reason = "review", "No sustained musical activity reached the entry threshold."
    elif raw_removal < minimum_trim_seconds:
        status = "no_trim"
        proposed_effective = duration
        raw_removal = 0.0
    elif raw_removal > max_proposed_removal_seconds:
        status, reason = "review", f"The proposed removal exceeds the safe {max_proposed_removal_seconds:.0f} second limit."
        proposed_effective = duration
        raw_removal = 0.0
    tail_levels = [item for item in levels if float(item["startSeconds"]) >= last_activity_end]
    decay_levels = [item for item in tail_levels if exit_threshold_db > float(item["rmsDb"]) >= noise_floor_db + 8.0]
    residual_noise_levels = [item for item in tail_levels if float(item["rmsDb"]) < noise_floor_db + 8.0]
    if decay_levels:
        tail_classification = "decay_or_reverb_then_silence"
    elif residual_noise_levels:
        tail_classification = "terminal_silence_with_residual_noise"
    else:
        tail_classification = "terminal_silence"
    return {
        "analyzerVersion": analyzer_version,
        "originalDurationSeconds": round(duration, 6),
        "adaptiveTailSeconds": round(detected_tail, 6),
        "conservativeTailSeconds": round(detected_tail, 6),
        "detectedTailSeconds": round(detected_tail, 6),
        "retainedTailSeconds": round(tail_padding_seconds, 6),
        "proposedRemovalSeconds": round(raw_removal, 6),
        "effectiveDurationSeconds": round(max(0.001, proposed_effective), 6),
        "adaptiveReachesEnd": True,
        "conservativeReachesEnd": True,
        "disagreementSeconds": 0.0,
        "confidence": "high" if status in {"confident", "no_trim"} else "review",
        "status": status,
        "reason": reason,
        "passes": {"windowed": {"windows": len(levels), "enterWindows": enter_windows, "releaseWindows": release_windows}},
        "windowedAnalysis": {
            "method": "pcm-rms-window-hysteresis",
            "windowSeconds": window_seconds,
            "sampleRate": sample_rate,
            "enterThresholdDb": enter_threshold_db,
            "exitThresholdDb": exit_threshold_db,
            "releaseHoldSeconds": release_hold_seconds,
            "minimumActiveSeconds": minimum_active_seconds,
            "tailPaddingSeconds": tail_padding_seconds,
            "minimumTrimSeconds": minimum_trim_seconds,
            "noiseFloorDb": round(noise_floor_db, 3),
            "lastActivityEndSeconds": round(last_activity_end, 6),
            "tailWindowCount": len(tail_levels),
            "decayWindowCount": len(decay_levels),
            "residualNoiseWindowCount": len(residual_noise_levels),
            "ignoredIsolatedEvents": ignored_isolated_events,
            "tailClassification": tail_classification,
        },
    }


def analyze_trailing_silence(
    path: Path,
    *,
    minimum_seconds: float = MIN_SILENCE_BLOCK_SECONDS,
    adaptive_noise_db: float = ADAPTIVE_NOISE_DB,
    conservative_noise_db: float = CONSERVATIVE_NOISE_DB,
    analyzer_version: str = SILENCE_ANALYZER_VERSION,
    runner: Callable[[Path, float, float], dict[str, Any]] | None = None,
    window_seconds: float = ANALYSIS_WINDOW_SECONDS,
    sample_rate: int = ANALYSIS_SAMPLE_RATE,
    enter_threshold_db: float = ENTER_THRESHOLD_DB,
    exit_threshold_db: float = EXIT_THRESHOLD_DB,
    release_hold_seconds: float = RELEASE_HOLD_SECONDS,
    minimum_active_seconds: float = MINIMUM_ACTIVE_SECONDS,
    tail_padding_seconds: float = RETAINED_TAIL_SECONDS,
    minimum_trim_seconds: float = MINIMUM_TRIM_SECONDS,
    max_proposed_removal_seconds: float = MAX_PROPOSED_REMOVAL_SECONDS,
) -> dict[str, Any]:
    duration = _probe_duration(path)
    if runner is None:
        levels = _decode_window_levels(path, window_seconds=window_seconds, sample_rate=sample_rate)
        return _windowed_analysis(
            levels,
            duration,
            window_seconds=window_seconds,
            sample_rate=sample_rate,
            enter_threshold_db=enter_threshold_db,
            exit_threshold_db=exit_threshold_db,
            release_hold_seconds=release_hold_seconds,
            minimum_active_seconds=minimum_active_seconds,
            tail_padding_seconds=tail_padding_seconds,
            minimum_trim_seconds=minimum_trim_seconds,
            max_proposed_removal_seconds=max_proposed_removal_seconds,
            analyzer_version=analyzer_version,
        )
    # Keep the injected legacy runner available for deterministic compatibility tests;
    # production preparation always uses the windowed PCM path above.
    adaptive = runner(path, adaptive_noise_db, minimum_seconds)
    conservative = runner(path, conservative_noise_db, minimum_seconds)
    adaptive_tail, adaptive_reaches_end = _tail_from_pass(adaptive, duration)
    conservative_tail, conservative_reaches_end = _tail_from_pass(conservative, duration)
    disagreement = abs(adaptive_tail - conservative_tail)
    detected_tail = min(adaptive_tail, conservative_tail) if adaptive_tail and conservative_tail else max(adaptive_tail, conservative_tail)
    reaches_end = adaptive_reaches_end and conservative_reaches_end
    status = "confident"
    reason = None
    if not reaches_end:
        status, reason = "review", "The detected trailing block did not reach the file end in both passes."
    elif disagreement > REVIEW_DISAGREEMENT_SECONDS:
        status, reason = "review", f"Adaptive and conservative passes disagree by {disagreement:.2f} seconds."
    elif max(0.0, detected_tail - RETAINED_TAIL_SECONDS) > max_proposed_removal_seconds:
        status, reason = "review", f"The proposed removal exceeds the safe {max_proposed_removal_seconds:.0f} second limit."
    elif detected_tail <= RETAINED_TAIL_SECONDS:
        status = "no_trim"
    proposed_removal = max(0.0, detected_tail - RETAINED_TAIL_SECONDS) if status == "confident" else 0.0
    return {
        "analyzerVersion": analyzer_version,
        "originalDurationSeconds": round(duration, 6),
        "adaptiveTailSeconds": round(adaptive_tail, 6),
        "conservativeTailSeconds": round(conservative_tail, 6),
        "detectedTailSeconds": round(detected_tail, 6),
        "retainedTailSeconds": RETAINED_TAIL_SECONDS,
        "proposedRemovalSeconds": round(proposed_removal, 6),
        "effectiveDurationSeconds": round(max(0.001, duration - proposed_removal), 6),
        "adaptiveReachesEnd": adaptive_reaches_end,
        "conservativeReachesEnd": conservative_reaches_end,
        "disagreementSeconds": round(disagreement, 6),
        "confidence": "high" if status in {"confident", "no_trim"} else "review",
        "status": status,
        "reason": reason,
        "passes": {"adaptive": adaptive, "conservative": conservative},
    }


def _track_fingerprint(root: Path, track: dict[str, Any]) -> tuple[Path, str]:
    path = _inside(root, str(track["finalPath"]))
    if not path.is_file() or path.stat().st_size <= 0:
        raise VideoPreparationError(f"Final Instrumental is missing for Track {track.get('sequence', '?')}.")
    return path, sha256(path)


def _apply_override(analysis: dict[str, Any], override: Any) -> dict[str, Any]:
    if override in {"keep-full", "keepFull"}:
        return {**analysis, "effectiveDurationSeconds": analysis["originalDurationSeconds"], "proposedRemovalSeconds": 0.0, "status": "override_keep_full", "confidence": "manual"}
    if override in {"trim", "trim-proposed"} and analysis.get("status") == "confident":
        return {**analysis, "status": "override_trim", "confidence": "manual"}
    return analysis


def prepare_video_config(root: Path, config: dict[str, Any], current_tracks: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    preparation = merge_preparation(config.get("preparation"))
    settings = preparation["settings"]
    artwork_record = (config.get("assets") or {}).get("artwork") if isinstance(config.get("assets"), dict) else None
    if not isinstance(artwork_record, dict):
        raise VideoPreparationError("Original artwork is not registered.")
    artwork = prepare_artwork(root, artwork_record, str(preparation.get("artworkMode") or "Auto"))
    texture = ensure_raster_texture(root)
    cache = preparation.get("analysisCache") if isinstance(preparation.get("analysisCache"), dict) else {}
    overrides = preparation.get("trackOverrides") if isinstance(preparation.get("trackOverrides"), dict) else {}
    settings_fingerprint = hashlib.sha256(json.dumps({"settings": settings, "trackOverrides": overrides}, sort_keys=True).encode("utf-8")).hexdigest()
    prepared_tracks: list[dict[str, Any]] = []
    seconds_removed = 0.0
    review_count = 0
    for track in current_tracks:
        path, fingerprint = _track_fingerprint(root, track)
        cached = cache.get(track["trackId"]) if isinstance(cache.get(track["trackId"]), dict) else None
        cache_valid = bool(cached and cached.get("sourceFingerprint") == fingerprint and cached.get("analyzerVersion") == SILENCE_ANALYZER_VERSION and cached.get("settingsFingerprint") == settings_fingerprint and not force)
        if cache_valid:
            analysis = cached["analysis"]
        else:
            try:
                analysis = analyze_trailing_silence(
                    path,
                    minimum_seconds=float(settings.get("minimumSilenceSeconds", MIN_SILENCE_BLOCK_SECONDS)),
                    adaptive_noise_db=float(settings.get("adaptiveNoiseDb", ADAPTIVE_NOISE_DB)),
                    conservative_noise_db=float(settings.get("conservativeNoiseDb", CONSERVATIVE_NOISE_DB)),
                    window_seconds=float(settings.get("windowSeconds", ANALYSIS_WINDOW_SECONDS)),
                    sample_rate=int(settings.get("analysisSampleRate", ANALYSIS_SAMPLE_RATE)),
                    enter_threshold_db=float(settings.get("enterThresholdDb", ENTER_THRESHOLD_DB)),
                    exit_threshold_db=float(settings.get("exitThresholdDb", EXIT_THRESHOLD_DB)),
                    release_hold_seconds=float(settings.get("releaseHoldSeconds", RELEASE_HOLD_SECONDS)),
                    minimum_active_seconds=float(settings.get("minimumActiveSeconds", MINIMUM_ACTIVE_SECONDS)),
                    tail_padding_seconds=float(settings.get("tailPaddingSeconds", settings.get("retainedTailSeconds", RETAINED_TAIL_SECONDS))),
                    minimum_trim_seconds=float(settings.get("minimumTrimSeconds", MINIMUM_TRIM_SECONDS)),
                    max_proposed_removal_seconds=float(settings.get("maxProposedRemovalSeconds", MAX_PROPOSED_REMOVAL_SECONDS)),
                )
            except VideoPreparationError as exc:
                analysis = {
                    "analyzerVersion": SILENCE_ANALYZER_VERSION,
                    "originalDurationSeconds": float(track["durationSeconds"]),
                    "adaptiveTailSeconds": 0.0,
                    "conservativeTailSeconds": 0.0,
                    "detectedTailSeconds": 0.0,
                    "retainedTailSeconds": float(settings.get("tailPaddingSeconds", settings.get("retainedTailSeconds", RETAINED_TAIL_SECONDS))),
                    "proposedRemovalSeconds": 0.0,
                    "effectiveDurationSeconds": float(track["durationSeconds"]),
                    "adaptiveReachesEnd": False,
                    "conservativeReachesEnd": False,
                    "disagreementSeconds": 0.0,
                    "confidence": "review",
                    "status": "review",
                    "reason": str(exc),
                    "passes": {},
                }
        override = overrides.get(track["trackId"])
        applied = _apply_override(analysis, override)
        if applied.get("status") == "review":
            review_count += 1
        seconds_removed += max(0.0, float(applied["originalDurationSeconds"]) - float(applied["effectiveDurationSeconds"]))
        cache[track["trackId"]] = {"sourceFingerprint": fingerprint, "analyzerVersion": SILENCE_ANALYZER_VERSION, "settingsFingerprint": settings_fingerprint, "analysis": analysis, "updatedAt": utc_now()}
        prepared_tracks.append({
            **track,
            "durationSeconds": float(applied["effectiveDurationSeconds"]),
            "originalDurationSeconds": float(applied["originalDurationSeconds"]),
            "effectiveDurationSeconds": float(applied["effectiveDurationSeconds"]),
            "trailingSilenceSeconds": float(applied["detectedTailSeconds"]),
            "retainedTailSeconds": float(applied["retainedTailSeconds"]),
            "proposedRemovalSeconds": float(applied["proposedRemovalSeconds"]),
            "silenceStatus": applied["status"],
            "silenceConfidence": applied["confidence"],
            "silenceReason": applied.get("reason"),
            "silenceAnalysis": applied,
            "preparationOverride": override,
            "finalFingerprint": fingerprint,
        })
    preparation.update({
        "schemaVersion": PREPARATION_SCHEMA_VERSION,
        "artwork": artwork,
        "texture": texture,
        "analysisCache": cache,
        "summary": {"tracksAnalyzed": len(prepared_tracks), "secondsRemoved": round(seconds_removed, 6), "reviewCount": review_count},
        "status": "review" if review_count else "ready",
        "settingsFingerprint": settings_fingerprint,
        "updatedAt": utc_now(),
    })
    return {**config, "schemaVersion": 2, "assets": {**(config.get("assets") or {}), "artwork": artwork["source"], "effectiveArtwork": artwork["effective"], "texture": texture}, "tracks": prepared_tracks, "preparation": preparation, "provenance": {**(config.get("provenance") or {}), "preparation": {"schemaVersion": PREPARATION_SCHEMA_VERSION, "artworkSourceSha256": artwork["sourceSha256"], "artworkRecipeVersion": artwork["recipeVersion"], "textureRecipeVersion": TEXTURE_RECIPE_VERSION, "analyzerVersion": SILENCE_ANALYZER_VERSION, "preparedAt": utc_now()}}}


def preparation_needs_refresh(root: Path, config: dict[str, Any], current_tracks: list[dict[str, Any]]) -> bool:
    if int(config.get("schemaVersion", 1)) < 2:
        return False
    preparation = merge_preparation(config.get("preparation"))
    artwork = preparation.get("artwork") if isinstance(preparation.get("artwork"), dict) else {}
    source = artwork.get("source") if isinstance(artwork.get("source"), dict) else {}
    effective = artwork.get("effective") if isinstance(artwork.get("effective"), dict) else {}
    texture = preparation.get("texture") if isinstance(preparation.get("texture"), dict) else {}
    try:
        source_path = _inside(root, str(source.get("path") or ""))
        effective_path = _inside(root, str(effective.get("path") or ""))
        texture_path = _inside(root, str(texture.get("path") or ""))
    except VideoPreparationError:
        return True
    if not source_path.is_file() or sha256(source_path) != source.get("sha256") or not effective_path.is_file() or not texture_path.is_file():
        return True
    settings = preparation.get("settings") if isinstance(preparation.get("settings"), dict) else {}
    overrides = preparation.get("trackOverrides") if isinstance(preparation.get("trackOverrides"), dict) else {}
    expected_settings = hashlib.sha256(json.dumps({"settings": settings, "trackOverrides": overrides}, sort_keys=True).encode("utf-8")).hexdigest()
    if expected_settings != preparation.get("settingsFingerprint"):
        return True
    saved = {str(item.get("trackId")): item for item in config.get("tracks", []) if isinstance(item, dict)}
    cache = preparation.get("analysisCache") if isinstance(preparation.get("analysisCache"), dict) else {}
    for current in current_tracks:
        item = saved.get(str(current.get("trackId")))
        if not item or any(item.get(key) != current.get(key) for key in ("trackId", "outputId", "finalPath", "fileFingerprint")):
            return True
        try:
            path, fingerprint = _track_fingerprint(root, current)
        except VideoPreparationError:
            return True
        if item.get("finalFingerprint") != fingerprint:
            return True
        cached = cache.get(str(current.get("trackId")))
        if not isinstance(cached, dict) or cached.get("sourceFingerprint") != fingerprint or cached.get("analyzerVersion") != SILENCE_ANALYZER_VERSION or cached.get("settingsFingerprint") != expected_settings:
            return True
    return len(saved) != len(current_tracks)


def migrate_video_config(manifest: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    if int(raw.get("schemaVersion", 1)) >= 2:
        return {**raw, "preparation": merge_preparation(raw.get("preparation"))}
    preparation = default_preparation()
    preparation["migration"] = {"fromSchemaVersion": int(raw.get("schemaVersion", 1)), "toSchemaVersion": 2, "migratedAt": utc_now(), "explicit": True}
    colors = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    default_colors = {"primary": "#B74633", "secondary": "#F7F3EA", "accent": "#B74633", "marker": "#D99A59", "scrim": "#22201F"}
    return {**raw, "schemaVersion": 2, "colors": {**default_colors, **colors}, "preparation": preparation, "migration": preparation["migration"]}


def refresh_video_preparation(manifest_path: str | Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from .projects import load_manifest, normalized_path, project_mutation_lock, save_manifest

    path = normalized_path(manifest_path)
    root = path.parent
    config_path = root / "video" / "config.json"
    if not config_path.is_file():
        raise VideoPreparationError("Configure the Video assets before refreshing preparation.")
    with project_mutation_lock(root):
        manifest = load_manifest(path)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise VideoPreparationError("Video configuration is not a JSON object.")
        config = migrate_video_config(manifest, raw)
        payload = payload or {}
        preparation = merge_preparation(config.get("preparation"))
        if payload.get("artworkMode") in {"Auto", "Original"}:
            preparation["artworkMode"] = payload["artworkMode"]
        if isinstance(payload.get("trackOverrides"), dict):
            preparation["trackOverrides"] = payload["trackOverrides"]
        if isinstance(payload.get("settings"), dict):
            preparation["settings"].update(payload["settings"])
        config["preparation"] = preparation
        current_tracks, issues = _current_tracks_for_preparation(manifest, root)
        if issues:
            raise VideoPreparationError("Video preparation is blocked: " + "; ".join(issues))
        prepared = prepare_video_config(root, config, current_tracks, force=bool(payload.get("force")))
        atomic_write_json(config_path, prepared)
        manifest["video"] = {"status": "configured", "configPath": "video/config.json", "schemaVersion": 2, "preparationUpdatedAt": utc_now()}
        save_manifest(manifest, root)
    from .video import build_video_state

    return build_video_state(path, _skip_prepare=True)


def _current_tracks_for_preparation(manifest: dict[str, Any], root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    from .video import _current_track_snapshot

    return _current_track_snapshot(manifest, root)
