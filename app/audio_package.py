from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .json_store import atomic_write_json, read_json
from .projects import ProjectError, normalized_path, utc_now
from .video import build_video_state
from .video_proof import require_approved_proof


AUDIO_PACKAGE_SCHEMA_VERSION = 1
AUDIO_RECIPE_VERSION = "full-album-mp3-v1"
AUDIO_ROOT = Path("audio")
AUDIO_PACKAGE_ROOT = AUDIO_ROOT / "packages"
AUDIO_POINTER = AUDIO_ROOT / "current.json"
AUDIO_JOB_ROOT = Path(".stem-comparison") / "audio-package-jobs"
AUDIO_FPS = 30
AUDIO_SAMPLE_RATE = 44_100
AUDIO_BITRATE = 320_000
AUDIO_DURATION_TOLERANCE_SECONDS = 0.12


class AudioPackageError(ProjectError):
    pass


class AudioPackageCancelled(AudioPackageError):
    pass


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    import hashlib

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _inside(root: Path, value: str | Path) -> Path:
    base = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise AudioPackageError("Audio package paths must remain inside the Project Folder.") from exc
    return resolved


def _run_capture(args: list[str], *, check_message: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise AudioPackageError(check_message) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or check_message).strip()
        raise AudioPackageError(detail[-2000:])
    return result


def _ffprobe(path: Path) -> dict[str, Any]:
    result = _run_capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check_message="FFprobe is required to validate the MP3 package.",
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AudioPackageError("FFprobe returned unreadable metadata.") from exc
    if not isinstance(value, dict):
        raise AudioPackageError("FFprobe returned an invalid metadata object.")
    return value


def _ffmpeg_version() -> str:
    result = _run_capture(["ffmpeg", "-hide_banner", "-version"], check_message="FFmpeg is required for MP3 export.")
    return (result.stdout or "").splitlines()[0].strip()


def _set_process(record: dict[str, Any] | None, process: subprocess.Popen[bytes]) -> None:
    if record is not None:
        record["process"] = process


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        process.terminate()


def _run_ffmpeg(
    args: list[str],
    *,
    record: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
    message: str = "FFmpeg failed.",
) -> str:
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise AudioPackageError("FFmpeg could not start. Verify that the required encoder is installed.") from exc
    _set_process(record, process)
    while process.poll() is None:
        if cancelled and cancelled():
            _terminate_process(process)
            process.communicate()
            raise AudioPackageCancelled("Audio export cancelled; no partial package was promoted.")
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    if record is not None:
        record.pop("process", None)
    if process.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        raise AudioPackageError((detail or message)[-4000:])
    return (stderr or stdout or b"").decode("utf-8", errors="replace")


def _metadata_defaults(state: dict[str, Any]) -> dict[str, str]:
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    artist = str(config.get("artist") or "Artist")
    album = str(config.get("album") or "Album")
    return {
        "title": album,
        "artist": artist,
        "album": album,
        "albumArtist": artist,
        "year": "",
        "genre": "Instrumental",
        "comment": "Full album instrumental mix",
    }


def _cover_source(root: Path, state: dict[str, Any], options: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    choice = str(options.get("coverChoice") or "artwork").strip().lower()
    if choice in {"none", ""}:
        return None, {"choice": "none", "path": None, "sha256": None}
    if choice == "custom":
        raw = str(options.get("customCoverPath") or "").strip()
        if not raw:
            raise AudioPackageError("A custom cover path is required when custom cover is selected.")
        candidate = Path(raw)
        source = candidate.resolve(strict=False) if candidate.is_absolute() else _inside(root, raw)
    elif choice == "thumbnail":
        source = None
        releases = root / "video" / "releases"
        for manifest_path in sorted(releases.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            manifest = read_json(manifest_path, None)
            if not isinstance(manifest, dict):
                continue
            candidate = manifest_path.parent / "thumbnail.png"
            if candidate.is_file():
                source = candidate
                break
        if source is None:
            raise AudioPackageError("The current Video thumbnail is not available for cover selection.")
    else:
        artwork = (state.get("assets") or {}).get("artwork") if isinstance(state.get("assets"), dict) else None
        raw = artwork.get("path") if isinstance(artwork, dict) else None
        if not raw:
            raise AudioPackageError("The current project artwork is not available for cover selection.")
        source = _inside(root, str(raw))
        choice = "artwork"
    if not source.is_file() or source.stat().st_size <= 0:
        raise AudioPackageError("The selected cover image is missing or empty.")
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise AudioPackageError("The selected cover must be a PNG or JPEG image.")
    try:
        source_path = source.relative_to(root.resolve()).as_posix()
        source_type = "project"
    except ValueError:
        source_path = str(source)
        source_type = "external"
    return source, {"choice": choice, "sourcePath": source_path, "sourceType": source_type, "sha256": _sha256(source), "bytes": source.stat().st_size}


def build_audio_snapshot(project_manifest: str | Path, options: dict[str, Any] | None = None, *, synthetic: bool = False) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    options = dict(options or {})
    if not synthetic:
        require_approved_proof(root)
    state = build_video_state(manifest_path, _skip_prepare=True)
    if not state.get("ready"):
        raise AudioPackageError("Audio export is blocked until the current Video timeline is ready.")
    composition = state.get("composition") if isinstance(state.get("composition"), dict) else {}
    timeline = composition.get("timeline") if isinstance(composition.get("timeline"), list) else []
    if not timeline:
        raise AudioPackageError("The approved effective timeline has no Tracks.")
    fps = int(composition.get("fps") or AUDIO_FPS)
    tracks: list[dict[str, Any]] = []
    for item in timeline:
        final_path = _inside(root, str(item.get("finalPath") or ""))
        if not final_path.is_file() or final_path.stat().st_size <= 0:
            raise AudioPackageError(f"Final Instrumental is missing for Track {item.get('sequence', '?')}.")
        duration_frames = int(item.get("durationInFrames") or 0)
        start_frame = int(item.get("startFrame") or 0)
        if duration_frames <= 0:
            raise AudioPackageError(f"Track {item.get('sequence', '?')} has no frame-authoritative duration.")
        tracks.append(
            {
                "trackId": str(item.get("trackId")),
                "sequence": int(item.get("sequence") or len(tracks) + 1),
                "title": str(item.get("title") or f"Track {len(tracks) + 1}"),
                "finalPath": final_path.relative_to(root).as_posix(),
                "fileFingerprint": _sha256(final_path),
                "startFrame": start_frame,
                "durationInFrames": duration_frames,
                "startSeconds": round(start_frame / fps, 6),
                "sourceStartSeconds": 0.0,
                "durationSeconds": round(duration_frames / fps, 6),
            }
        )
    metadata = _metadata_defaults(state)
    metadata.update({key: str(options[key]) for key in metadata if key in options and options[key] is not None})
    cover_source, cover = _cover_source(root, state, options)
    preparation = state.get("preparation") if isinstance(state.get("preparation"), dict) else {}
    settings = preparation.get("settings") if isinstance(preparation.get("settings"), dict) else {}
    fade_in = float(settings.get("audioFadeInSeconds") or 0)
    fade_out = float(settings.get("audioFadeOutSeconds") or 0)
    expected_duration = round(sum(float(item["durationSeconds"]) for item in tracks), 6)
    source_manifest_sha = _sha256(manifest_path)
    timeline_payload = {
        "fps": fps,
        "fadeInSeconds": fade_in,
        "fadeOutSeconds": fade_out,
        "tracks": tracks,
        "metadata": metadata,
        "cover": cover,
        "encoder": {"codec": "libmp3lame", "bitrate": AUDIO_BITRATE, "sampleRate": AUDIO_SAMPLE_RATE, "channels": 2},
    }
    snapshot = {
        "schemaVersion": AUDIO_PACKAGE_SCHEMA_VERSION,
        "recipeVersion": AUDIO_RECIPE_VERSION,
        "projectFolder": str(root),
        "projectManifestSha256": source_manifest_sha,
        "timelineFingerprint": _digest({"fps": fps, "fadeInSeconds": fade_in, "fadeOutSeconds": fade_out, "tracks": tracks}),
        "fps": fps,
        "fadeInSeconds": fade_in,
        "fadeOutSeconds": fade_out,
        "expectedDurationSeconds": expected_duration,
        "tracks": tracks,
        "metadata": metadata,
        "cover": cover,
        "settings": {"bitrate": AUDIO_BITRATE, "sampleRate": AUDIO_SAMPLE_RATE, "channels": 2, "durationToleranceSeconds": AUDIO_DURATION_TOLERANCE_SECONDS},
        "timelinePayload": timeline_payload,
        "proof": {"proofId": (state.get("provenance") or {}).get("proofId"), "inputFingerprint": (state.get("provenance") or {}).get("proofInputFingerprint")},
        "syntheticFixture": synthetic,
    }
    snapshot["inputFingerprint"] = _digest({key: value for key, value in snapshot.items() if key != "inputFingerprint"})
    snapshot["optionsFingerprint"] = _digest({"metadata": metadata, "cover": cover, "encoder": snapshot["settings"]})
    if cover_source is not None:
        try:
            snapshot["cover"]["sourcePath"] = cover_source.relative_to(root.resolve()).as_posix()
        except ValueError:
            snapshot["cover"]["sourcePath"] = str(cover_source)
    return snapshot


def _human_title(value: str) -> str:
    return re.sub(r"^\s*\d{2}-\d{2}\s+", "", str(value).strip()).replace("_", " / ")


def _chapter_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _cue_time(seconds: float) -> str:
    total_frames = max(0, int(round(seconds * 75)))
    minutes, remainder = divmod(total_frames, 75 * 60)
    secs, frames = divmod(remainder, 75)
    return f"{minutes:02d}:{secs:02d}:{frames:02d}"


def _chapters_and_cue(snapshot: dict[str, Any]) -> tuple[str, str]:
    chapters = "\n".join(f"{_chapter_time(float(item['startSeconds']))} {int(item['sequence']):02d} {_human_title(item['title'])}" for item in snapshot["tracks"]) + "\n"
    metadata = snapshot["metadata"]
    lines = [f'TITLE "{metadata["album"]}"', f'PERFORMER "{metadata["artist"]}"', 'FILE "album-mix.mp3" MP3']
    for item in snapshot["tracks"]:
        lines.extend([
            f"  TRACK {int(item['sequence']):02d} AUDIO",
            f'    TITLE "{_human_title(item["title"])}"',
            f'    PERFORMER "{metadata["artist"]}"',
            f"    REM START_FRAME {int(item['startFrame'])}",
            f"    INDEX 01 {_cue_time(float(item['startSeconds']))}",
        ])
    return chapters, "\n".join(lines) + "\n"


def _redacted_command(args: list[str], source_paths: set[str], cover_path: str | None, output_path: str) -> list[str]:
    redacted: list[str] = []
    for value in args:
        if value in source_paths:
            redacted.append("<final-instrumental>")
        elif cover_path and value == cover_path:
            redacted.append("<cover>")
        elif value == output_path:
            redacted.append("<album-mix.mp3>")
        else:
            redacted.append(value)
    return redacted


def _parse_loudness(log: str) -> dict[str, float | None]:
    def match(pattern: str) -> float | None:
        found = re.search(pattern, log, flags=re.IGNORECASE)
        return float(found.group(1)) if found else None

    return {
        "integratedLufs": match(r"(?:Integrated loudness:.*?I:|\bI:)\s*(-?\d+(?:\.\d+)?)\s*LUFS"),
        "loudnessRangeLu": match(r"(?:Loudness range:.*?LRA:|\bLRA:)\s*(-?\d+(?:\.\d+)?)\s*LU"),
        "truePeakDbfs": match(r"(?:True peak:.*?Peak:|\bPeak:)\s*(-?\d+(?:\.\d+)?)\s*dBFS"),
    }


def _packet_timestamps(path: Path) -> tuple[bool, list[float]]:
    result = _run_capture(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)], check_message="FFprobe could not inspect MP3 timestamps.")
    values: list[float] = []
    for line in result.stdout.splitlines():
        value = line.strip().split(",", 1)[0]
        try:
            if value:
                values.append(float(value))
        except ValueError:
            continue
    return bool(values) and all(right + 1e-7 >= left for left, right in zip(values, values[1:])), values


def _rms_db(samples: bytes) -> float:
    import array

    values = array.array("h")
    values.frombytes(samples)
    if not values:
        return -120.0
    mean_square = sum(float(value) ** 2 for value in values) / len(values)
    return 10 * math.log10(max(mean_square, 1) / (32768.0**2))


def _boundary_audio_checks(path: Path, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    boundaries = [float(item["startSeconds"]) for item in snapshot["tracks"] if float(item["startSeconds"]) > 0]
    for boundary in boundaries:
        start = max(0.0, boundary - 0.04)
        result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.6f}", "-t", "0.08", "-i", str(path), "-f", "s16le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2", "-"], capture_output=True, check=False)
        if result.returncode != 0:
            raise AudioPackageError("The MP3 boundary decode could not be validated.")
        rms = _rms_db(result.stdout)
        checks.append({"boundarySeconds": boundary, "windowSeconds": [start, start + 0.08], "rmsDb": round(rms, 3), "nonSilent": rms > -80.0})
    return checks


def _validate_mp3(path: Path, snapshot: dict[str, Any], chapters: str, cue: str, *, ffmpeg_log: str) -> dict[str, Any]:
    probe = _ffprobe(path)
    streams = [item for item in probe.get("streams", []) if isinstance(item, dict) and item.get("codec_type") == "audio"]
    stream = streams[0] if streams else {}
    format_value = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    duration = float(format_value.get("duration") or stream.get("duration") or 0)
    bit_rate = int(float(stream.get("bit_rate") or format_value.get("bit_rate") or 0))
    timestamps_ok, timestamps = _packet_timestamps(path)
    tags = format_value.get("tags") if isinstance(format_value.get("tags"), dict) else {}
    expected_tags = {"title": snapshot["metadata"]["title"], "artist": snapshot["metadata"]["artist"], "album": snapshot["metadata"]["album"], "album_artist": snapshot["metadata"]["albumArtist"], "genre": snapshot["metadata"]["genre"], "comment": snapshot["metadata"]["comment"]}
    if snapshot["metadata"].get("year"):
        expected_tags["date"] = snapshot["metadata"]["year"]
    tags_ok = all(str(tags.get(key) or "") == value for key, value in expected_tags.items())
    boundary_checks = _boundary_audio_checks(path, snapshot)
    boundary_ok = all(bool(item["nonSilent"]) for item in boundary_checks)
    loudness = _parse_loudness(ffmpeg_log)
    loudness_ok = all(value is not None for value in loudness.values())
    expected_duration = float(snapshot["expectedDurationSeconds"])
    checks = {
        "codec": stream.get("codec_name") == "mp3",
        "bitrate": abs(bit_rate - AUDIO_BITRATE) <= 1000,
        "sampleRate": int(stream.get("sample_rate") or 0) == AUDIO_SAMPLE_RATE,
        "channels": int(stream.get("channels") or 0) == 2,
        "duration": abs(duration - expected_duration) <= AUDIO_DURATION_TOLERANCE_SECONDS,
        "timestampsMonotonic": timestamps_ok,
        "tags": tags_ok,
        "chapters": bool(chapters.strip()) and chapters.count("\n") == len(snapshot["tracks"]),
        "cue": bool(cue.startswith("TITLE ")) and cue.count("TRACK ") == len(snapshot["tracks"]),
        "boundaryAudio": boundary_ok,
        "loudnessAnalysis": loudness_ok,
    }
    if not all(checks.values()):
        failed = ", ".join(key for key, value in checks.items() if not value)
        raise AudioPackageError(f"MP3 validation failed: {failed}.")
    return {
        "checks": checks,
        "ffprobe": probe,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "durationSeconds": duration,
        "expectedDurationSeconds": expected_duration,
        "durationToleranceSeconds": AUDIO_DURATION_TOLERANCE_SECONDS,
        "timestamps": {"count": len(timestamps), "first": timestamps[0] if timestamps else None, "last": timestamps[-1] if timestamps else None, "monotonic": timestamps_ok},
        "boundaryChecks": boundary_checks,
        "loudness": loudness,
    }


def _audio_artifact(root: Path, path: Path, relative: str) -> dict[str, Any]:
    return {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _next_package_id(root: Path, fingerprint: str) -> tuple[str, int]:
    packages = root / AUDIO_PACKAGE_ROOT
    packages.mkdir(parents=True, exist_ok=True)
    prefix = f"mp3-{fingerprint[:12]}-v"
    versions: list[int] = []
    for manifest in packages.glob(f"{prefix}*/manifest.json"):
        match = re.fullmatch(re.escape(prefix) + r"(\d+)", manifest.parent.name)
        if match:
            versions.append(int(match.group(1)))
    version = max(versions, default=0) + 1
    return f"{prefix}{version}", version


def _cached_package(root: Path, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    packages = root / AUDIO_PACKAGE_ROOT
    candidates = sorted(packages.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for manifest_path in candidates:
        manifest = read_json(manifest_path, None)
        if not isinstance(manifest, dict) or manifest.get("status") != "ready":
            continue
        if manifest.get("inputFingerprint") != snapshot.get("inputFingerprint") or manifest.get("optionsFingerprint") != snapshot.get("optionsFingerprint"):
            continue
        result = read_current_audio_package(root, package_id=str(manifest.get("packageId")))
        if result.get("ready"):
            return result | {"cached": True}
    return None


def generate_audio_package(
    root: Path,
    snapshot: dict[str, Any],
    *,
    staging_root: Path | None = None,
    package_id: str | None = None,
    package_version: int | None = None,
    record: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
    status_callback: Callable[[str, float, str], None] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    package_id = package_id or f"mp3-{str(snapshot['inputFingerprint'])[:12]}-v{package_version or 1}"
    staging_root = staging_root or root / AUDIO_JOB_ROOT / package_id / "staging" / "package"
    staging_root.mkdir(parents=True, exist_ok=False)
    package_root = root / AUDIO_PACKAGE_ROOT / package_id
    if package_root.exists():
        raise AudioPackageError("The Audio Mix Package destination already exists.")
    if status_callback:
        status_callback("assembling", 0.15, "Assembling the approved Track timeline.")
    source_paths = {_inside(root, item["finalPath"]) for item in snapshot["tracks"]}
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    input_args: list[str] = []
    ordered_sources = [_inside(root, item["finalPath"]) for item in snapshot["tracks"]]
    for source in ordered_sources:
        input_args.extend(["-i", str(source)])
    filters: list[str] = []
    for index, item in enumerate(snapshot["tracks"]):
        filters.append(f"[{index}:a]atrim=start={float(item.get('sourceStartSeconds', 0.0)):.6f}:duration={float(item['durationSeconds']):.6f},asetpts=PTS-STARTPTS[a{index}]")
    joined = "".join(f"[a{index}]" for index in range(len(snapshot["tracks"])))
    filters.append(f"{joined}concat=n={len(snapshot['tracks'])}:v=0:a=1[joined]")
    audio_chain = "[joined]aresample=44100:resampler=soxr,aformat=sample_rates=44100:channel_layouts=stereo"
    fade_in = float(snapshot.get("fadeInSeconds") or 0)
    fade_out = float(snapshot.get("fadeOutSeconds") or 0)
    if fade_in > 0:
        audio_chain += f",afade=t=in:st=0:d={fade_in:.6f}"
    if fade_out > 0:
        fade_start = max(0.0, float(snapshot["expectedDurationSeconds"]) - fade_out)
        audio_chain += f",afade=t=out:st={fade_start:.6f}:d={fade_out:.6f}"
    filters.append(audio_chain + "[out]")
    output_path = staging_root / "album-mix.mp3"
    cover_path = None
    cover = snapshot.get("cover") if isinstance(snapshot.get("cover"), dict) else {}
    if cover.get("choice") not in {None, "none"}:
        cover_source = str(cover.get("sourcePath") or "")
        cover_path = Path(cover_source).resolve(strict=False) if cover.get("sourceType") == "external" else _inside(root, cover_source)
        if not cover_path.is_file() or cover_path.stat().st_size <= 0:
            raise AudioPackageError("The selected cover image is missing or empty.")
        shutil.copy2(cover_path, staging_root / cover_path.name)
    args.extend(input_args)
    if cover_path is not None:
        args.extend(["-i", str(cover_path)])
    args.extend(["-filter_complex", ";".join(filters), "-map", "[out]"])
    if cover_path is not None:
        args.extend(["-map", f"{len(ordered_sources)}:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"])
    args.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2", "-map_metadata", "-1", "-id3v2_version", "3", "-write_id3v1", "1"])
    metadata = snapshot["metadata"]
    for key, value in (("title", metadata["title"]), ("artist", metadata["artist"]), ("album", metadata["album"]), ("album_artist", metadata["albumArtist"]), ("date", metadata.get("year", "")), ("genre", metadata["genre"]), ("comment", metadata["comment"])):
        if value:
            args.extend(["-metadata", f"{key}={value}"])
    args.extend(["-f", "mp3", str(output_path)])
    ffmpeg_log = _run_ffmpeg(args, record=record, cancelled=cancelled, message="The MP3 encoder failed.")
    if status_callback:
        status_callback("validating", 0.7, "Validating MP3 metadata, boundaries and loudness.")
    chapters, cue = _chapters_and_cue(snapshot)
    (staging_root / "chapters.txt").write_text(chapters, encoding="utf-8")
    (staging_root / "album.cue").write_text(cue, encoding="utf-8")
    validation = _validate_mp3(output_path, snapshot, chapters, cue, ffmpeg_log=ffmpeg_log + "\n" + _run_capture(["ffmpeg", "-hide_banner", "-i", str(output_path), "-filter_complex", "ebur128=framelog=verbose:peak=true", "-f", "null", "-"], check_message="Loudness analysis could not run.").stderr)
    artifacts = {"albumMix": _audio_artifact(root, output_path, "album-mix.mp3"), "chapters": _audio_artifact(root, staging_root / "chapters.txt", "chapters.txt"), "cue": _audio_artifact(root, staging_root / "album.cue", "album.cue")}
    if cover_path is not None:
        cover_name = cover_path.name
        artifacts["cover"] = _audio_artifact(root, staging_root / cover_name, cover_name)
    manifest = {
        "schemaVersion": AUDIO_PACKAGE_SCHEMA_VERSION,
        "packageId": package_id,
        "packageVersion": package_version,
        "kind": "audio-mix-package",
        "status": "ready",
        "recipeVersion": AUDIO_RECIPE_VERSION,
        "inputFingerprint": snapshot["inputFingerprint"],
        "optionsFingerprint": snapshot["optionsFingerprint"],
        "timelineFingerprint": snapshot["timelineFingerprint"],
        "projectManifestSha256": snapshot["projectManifestSha256"],
        "metadata": metadata,
        "cover": snapshot.get("cover"),
        "timeline": snapshot["tracks"],
        "settings": snapshot["settings"],
        "analysis": validation["loudness"],
        "validation": validation,
        "artifacts": artifacts,
        "provenance": {
            "source": "current approved Final Instrumentals only",
            "historicalOutputsUsed": False,
            "separationInvoked": False,
            "ffmpegVersion": _ffmpeg_version(),
            "encoder": "libmp3lame",
            "commandRecipe": _redacted_command([str(value) for value in args], {str(path) for path in ordered_sources}, str(cover_path) if cover_path else None, str(output_path)),
            "durationToleranceSeconds": AUDIO_DURATION_TOLERANCE_SECONDS,
            "frameAuthoritative": True,
        },
        "createdAt": utc_now(),
    }
    atomic_write_json(staging_root / "manifest.json", manifest)
    if status_callback:
        status_callback("promoting", 0.95, "Promoting the validated Audio Mix Package.")
    package_root.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_root, package_root)
    atomic_write_json(root / AUDIO_POINTER, {"schemaVersion": AUDIO_PACKAGE_SCHEMA_VERSION, "packageId": package_id, "inputFingerprint": snapshot["inputFingerprint"], "updatedAt": utc_now()})
    if status_callback:
        status_callback("complete", 1.0, "Audio Mix Package ready.")
    return read_current_audio_package(root, package_id=package_id)


def _package_manifest_path(root: Path, package_id: str) -> Path:
    if not re.fullmatch(r"mp3-[0-9a-f]{12}-v\d+", package_id):
        raise AudioPackageError("The Audio Mix Package ID is invalid.")
    return root / AUDIO_PACKAGE_ROOT / package_id / "manifest.json"


def read_current_audio_package(root: Path, *, package_id: str | None = None) -> dict[str, Any]:
    pointer = read_json(root / AUDIO_POINTER, None)
    selected = package_id or (pointer.get("packageId") if isinstance(pointer, dict) else None)
    if not selected:
        return {"ready": False, "status": "missing", "packageFolder": str(root / AUDIO_PACKAGE_ROOT), "issues": ["No Audio Mix Package has been generated."]}
    manifest_path = _package_manifest_path(root, str(selected))
    manifest = read_json(manifest_path, None)
    if not isinstance(manifest, dict):
        return {"ready": False, "status": "blocked", "packageFolder": str(manifest_path.parent), "manifestPath": str(manifest_path), "issues": ["Audio Mix Package manifest is unreadable."]}
    issues: list[str] = []
    if manifest.get("status") != "ready":
        issues.append("Audio Mix Package is not marked ready.")
    for key, record in (manifest.get("artifacts") or {}).items():
        if not isinstance(record, dict):
            issues.append(f"Audio artifact {key} record is invalid.")
            continue
        path = manifest_path.parent / str(record.get("path") or "")
        if not path.is_file() or _sha256(path) != record.get("sha256"):
            issues.append(f"Audio artifact {key} is missing or changed.")
    return {"ready": not issues, "status": "ready" if not issues else "blocked", "packageId": manifest.get("packageId"), "packageFolder": str(manifest_path.parent), "manifestPath": str(manifest_path), "artifacts": {key: {**record, "path": str(manifest_path.parent / str(record.get("path") or ""))} for key, record in (manifest.get("artifacts") or {}).items() if isinstance(record, dict)}, "manifest": manifest, "issues": issues}


def _status_path(root: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise AudioPackageError("The audio job ID is invalid.")
    return root / AUDIO_JOB_ROOT / job_id / "status.json"


def _write_status(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    atomic_write_json(path, value)
    return value


def _active_audio_job(root: Path) -> str | None:
    for status_path in (root / AUDIO_JOB_ROOT).glob("*/status.json"):
        status = read_json(status_path, None)
        if isinstance(status, dict) and status.get("status") in {"queued", "running", "stopping"}:
            return str(status.get("jobId") or status_path.parent.name)
    return None


def _active_external_job(root: Path) -> str | None:
    roots = [root / ".stem-comparison" / "work" / "video", root / ".stem-comparison" / "work" / "video-proof", root / ".stem-comparison" / "jobs"]
    for base in roots:
        for status_path in base.glob("*/status.json") if base.exists() else []:
            status = read_json(status_path, None)
            if isinstance(status, dict) and status.get("status") in {"queued", "running", "stopping"}:
                return str(status.get("jobId") or status_path.parent.name)
    return None


def _run_audio_job(root: Path, job_id: str, processes: dict[str, dict[str, Any]]) -> None:
    record = processes[job_id]
    status_path = record["statusPath"]
    job_dir = status_path.parent
    try:
        input_value = read_json(job_dir / "input.json", None)
        if not isinstance(input_value, dict):
            raise AudioPackageError("Audio export input snapshot is missing.")
        _write_status(status_path, {**read_json(status_path, {}), "status": "running", "stage": "assembling", "progress": 0.05, "message": "Assembling the approved Track timeline."})
        result = generate_audio_package(root, input_value["snapshot"], staging_root=job_dir / "staging" / "package", package_id=job_id_to_package_id(input_value["snapshot"], job_id), package_version=int(input_value["packageVersion"]), record=record, cancelled=lambda: bool(record.get("cancelRequested")), status_callback=lambda stage, progress, message: _write_status(status_path, {**read_json(status_path, {}), "status": "running", "stage": stage, "progress": progress, "message": message}))
        _write_status(status_path, {**read_json(status_path, {}), "status": "complete", "stage": "complete", "progress": 1, "message": "Audio Mix Package ready.", "promotedPath": result.get("packageFolder"), "manifestPath": result.get("manifestPath"), "manifest": result.get("manifest")})
    except AudioPackageCancelled as exc:
        shutil.rmtree(job_dir / "staging", ignore_errors=True)
        _write_status(status_path, {**read_json(status_path, {}), "status": "cancelled", "stage": "cancelled", "message": str(exc)})
    except Exception as exc:
        shutil.rmtree(job_dir / "staging", ignore_errors=True)
        _write_status(status_path, {**read_json(status_path, {}), "status": "failed", "stage": "failed", "message": str(exc), "error": f"{type(exc).__name__}: {exc}"})
    finally:
        record.pop("process", None)
        processes.pop(job_id, None)


def job_id_to_package_id(snapshot: dict[str, Any], job_id: str) -> str:
    return f"mp3-{str(snapshot['inputFingerprint'])[:12]}-v{int(snapshot.get('packageVersion') or 1)}"


def start_audio_export(project_manifest: str | Path, processes: dict[str, dict[str, Any]], options: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
    manifest_path = normalized_path(project_manifest)
    root = manifest_path.parent
    active = _active_audio_job(root) or _active_external_job(root)
    if active:
        raise AudioPackageError(f"An audio or video job {active} is already active.")
    snapshot = build_audio_snapshot(manifest_path, options, synthetic=False)
    if not force:
        cached = _cached_package(root, snapshot)
        if cached:
            return {"status": "cached", "cached": True, **cached}
    package_id, package_version = _next_package_id(root, str(snapshot["inputFingerprint"]))
    snapshot["packageVersion"] = package_version
    job_id = uuid.uuid4().hex
    job_dir = root / AUDIO_JOB_ROOT / job_id
    staging = job_dir / "staging" / "package"
    job_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(job_dir / "input.json", {"jobId": job_id, "snapshot": snapshot, "options": dict(options or {}), "packageVersion": package_version, "packageId": package_id, "force": force})
    status_path = job_dir / "status.json"
    status = _write_status(status_path, {"jobId": job_id, "kind": "real-full-album-mp3", "status": "queued", "stage": "queued", "progress": 0, "message": "Audio export queued.", "inputFingerprint": snapshot["inputFingerprint"], "packageId": package_id, "packageVersion": package_version, "stagingPath": str(staging), "promotedPath": None})
    record: dict[str, Any] = {"statusPath": status_path, "cancelRequested": False}
    processes[job_id] = record
    threading.Thread(target=_run_audio_job, args=(root, job_id, processes), daemon=True).start()
    return status


def read_audio_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    root = normalized_path(project_manifest).parent
    path = _status_path(root, job_id)
    status = read_json(path, None)
    if not isinstance(status, dict):
        raise AudioPackageError("The requested audio job does not exist.")
    if status.get("status") in {"queued", "running", "stopping"} and job_id not in processes:
        shutil.rmtree(path.parent / "staging", ignore_errors=True)
        status = _write_status(path, {**status, "status": "interrupted", "stage": "interrupted", "message": "Audio export interrupted by backend restart; no partial package was promoted."})
    return status


def stop_audio_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status = read_audio_job(project_manifest, job_id, processes)
    if status.get("status") not in {"queued", "running"}:
        return status
    record = processes.get(job_id)
    if not record:
        return read_audio_job(project_manifest, job_id, processes)
    record["cancelRequested"] = True
    process = record.get("process")
    if isinstance(process, subprocess.Popen):
        _terminate_process(process)
    return _write_status(record["statusPath"], {**status, "status": "stopping", "stage": "stopping", "message": "Stopping MP3 export without promotion."})


def retry_audio_job(project_manifest: str | Path, job_id: str, processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    status = read_audio_job(project_manifest, job_id, processes)
    if status.get("status") not in {"failed", "cancelled", "interrupted"}:
        raise AudioPackageError("Only a failed, cancelled or interrupted audio export can be retried.")
    root = normalized_path(project_manifest).parent
    input_value = read_json(_status_path(root, job_id).parent / "input.json", None)
    options = input_value.get("options") if isinstance(input_value, dict) and isinstance(input_value.get("options"), dict) else {}
    return start_audio_export(project_manifest, processes, options, force=True)
