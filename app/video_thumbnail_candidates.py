from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .brand import brand_input_props, brand_snapshot_assets
from .json_store import atomic_write_json
from .projects import normalized_path, utc_now
from .video import build_video_state
from .video_render import VideoRenderError, _code_fingerprint, _inside, _node_path, _sha256, _terminate_process_tree, _validate_snapshot_current


NODE_SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "render-thumbnail-candidates.mjs"
CONTRACT_MODULE = Path(__file__).resolve()
LAYOUTS = frozenset({"control", "album-focus", "instrumental-focus"})
SLOTS = ("A", "B", "C")


class ThumbnailCandidateError(VideoRenderError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n" or struct.unpack(">I", handle.read(4))[0] != 13 or handle.read(4) != b"IHDR":
                return None
            return struct.unpack(">II", handle.read(8))
    except (OSError, struct.error):
        return None


def _asset(root: Path, record: object, role: str) -> tuple[Path, dict[str, object]]:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise ThumbnailCandidateError(f"Protected thumbnail asset {role} is not registered")
    path = _inside(root, root / str(record["path"]))
    if not path.is_file():
        raise ThumbnailCandidateError(f"Protected thumbnail asset {role} is missing")
    digest = _sha256(path)
    size = path.stat().st_size
    if record.get("sha256") != digest or int(record.get("bytes", -1)) != size:
        raise ThumbnailCandidateError(f"Protected thumbnail asset {role} changed")
    return path, {"relativePath": path.relative_to(root).as_posix(), "sha256": digest, "bytes": size}


def build_thumbnail_snapshot(project_manifest: str | Path) -> dict[str, object]:
    manifest_path = normalized_path(project_manifest)
    project_root = manifest_path.parent
    state = build_video_state(manifest_path, _skip_prepare=True)
    if not state.get("ready"):
        raise ThumbnailCandidateError("Thumbnail rendering is blocked: " + "; ".join(state.get("issues", [])))
    config = state["config"]
    configured_assets = config.get("assets") if isinstance(config.get("assets"), dict) else {}
    artwork_record = configured_assets.get("effectiveArtwork") or configured_assets.get("artwork")
    visual = {}
    visual_assets = [("artwork", artwork_record), ("displayFont", configured_assets.get("displayFont")), ("utilityFont", configured_assets.get("utilityFont"))]
    if "displayFontItalic" in configured_assets:
        visual_assets.insert(2, ("displayFontItalic", configured_assets.get("displayFontItalic")))
    for key, record in visual_assets:
        _path, visual[key] = _asset(project_root, record, key)
    texture_key = None
    if config.get("cinematicFinish") != "Off":
        _path, visual["texture"] = _asset(project_root, configured_assets.get("texture"), "texture")
        texture_key = "texture"
    visual.update(brand_snapshot_assets(project_root, config.get("brand") or {}))
    timeline = [
        {
            "trackId": item["trackId"],
            "sequence": int(item["sequence"]),
            "title": item["title"],
            "durationSeconds": float(item["durationSeconds"]),
            "startFrame": int(item["startFrame"]),
            "durationInFrames": int(item["durationInFrames"]),
            "outputId": item["outputId"],
            "fileFingerprint": item.get("fileFingerprint"),
        }
        for item in state["composition"]["timeline"]
    ]
    protected_fields = {
        "artist": config.get("artist"),
        "album": config.get("album"),
        "typography": config.get("typography"),
        "colors": config.get("colors"),
        "cinematicFinish": config.get("cinematicFinish"),
        "reducedMotion": bool(config.get("reducedMotion", False)),
        "brand": config.get("brand"),
        "timeline": timeline,
    }
    return {
        "snapshotVersion": 1,
        "kind": "thumbnail-candidates",
        "projectFolder": str(project_root),
        "projectManifest": "project.json",
        "configuration": "video/config.json",
        "codeFingerprint": _code_fingerprint(),
        "candidateContractSha256": _sha256(CONTRACT_MODULE),
        "candidateRendererSha256": _sha256(NODE_SCRIPT),
        "fingerprints": {"projectManifest": _sha256(project_root / "project.json"), "configuration": _sha256(project_root / "video" / "config.json"), "protectedTimeline": hashlib.sha256(_canonical(protected_fields)).hexdigest()},
        "assets": visual,
        "tracks": timeline,
        "expected": {"width": 1280, "height": 720, "format": "png", "maxBytes": 2 * 1024 * 1024},
        "props": {
            "artist": str(config.get("artist", "")),
            "album": str(config.get("album", "")),
            "displayFontFamily": config["typography"]["displayFontFamily"],
            "utilityFontFamily": config["typography"]["utilityFontFamily"],
            "colors": config["colors"],
            "cinematicFinish": config["cinematicFinish"],
            "reducedMotion": bool(config.get("reducedMotion", False)),
            "artworkKey": "artwork",
            "displayFontKey": "displayFont",
            "utilityFontKey": "utilityFont",
            **({"displayFontItalicKey": "displayFontItalic"} if "displayFontItalic" in visual else {}),
            "textureKey": texture_key,
            "tracks": timeline,
            "brand": brand_input_props(config.get("brand") or {}),
        },
    }


def _validate_variants(variants: object) -> list[dict[str, object]]:
    if not isinstance(variants, list) or len(variants) != 3:
        raise ThumbnailCandidateError("Exactly three thumbnail variants are required")
    normalized = []
    layouts = set()
    for expected_slot, item in zip(SLOTS, variants):
        if not isinstance(item, dict) or set(item) != {"slot", "override", "rationale"} or item.get("slot") != expected_slot:
            raise ThumbnailCandidateError("Thumbnail variants must be ordered A/B/C with typed fields only")
        override = item.get("override")
        if not isinstance(override, dict) or set(override) != {"layout"} or override.get("layout") not in LAYOUTS:
            raise ThumbnailCandidateError("Thumbnail editorial override is invalid")
        if not isinstance(item.get("rationale"), str) or not str(item["rationale"]).strip():
            raise ThumbnailCandidateError("Thumbnail rationale is required")
        layouts.add(str(override["layout"]))
        normalized.append({"slot": expected_slot, "override": {"layout": override["layout"]}, "rationale": str(item["rationale"]).strip()})
    if layouts != LAYOUTS:
        raise ThumbnailCandidateError("A/B/C must isolate all three declared visual layouts")
    return normalized


def _validate_existing(destination: Path, fingerprint: str) -> dict[str, object]:
    manifest_path = destination / "thumbnail-candidates.json"
    if not manifest_path.is_file():
        raise ThumbnailCandidateError("Existing candidate render has no canonical manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("renderFingerprint") != fingerprint:
        raise ThumbnailCandidateError("Existing candidate render identity differs")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or [item.get("slot") for item in outputs if isinstance(item, dict)] != list(SLOTS):
        raise ThumbnailCandidateError("Existing candidate render does not contain exact A/B/C outputs")
    expected_names = {"thumbnail-candidates.json", *(f"{slot}.png" for slot in SLOTS)}
    actual_names = {path.name for path in destination.iterdir()}
    if actual_names != expected_names:
        raise ThumbnailCandidateError("Existing candidate render contains unexpected files")
    for item in outputs:
        path = destination / str(item.get("filename", ""))
        if not path.is_file() or path.stat().st_size != item.get("bytes") or _sha256(path) != item.get("sha256"):
            raise ThumbnailCandidateError("Existing candidate PNG is missing or changed")
    return manifest | {"rootPath": str(destination), "manifestPath": str(manifest_path)}


def _validate_renderer_current(snapshot: dict[str, object]) -> None:
    if _sha256(CONTRACT_MODULE) != snapshot.get("candidateContractSha256"):
        raise ThumbnailCandidateError("Render snapshot is stale: the candidate contract changed before promotion")
    if not NODE_SCRIPT.is_file() or _sha256(NODE_SCRIPT) != snapshot.get("candidateRendererSha256"):
        raise ThumbnailCandidateError("Render snapshot is stale: the candidate renderer changed before promotion")


def render_thumbnail_candidates(
    project_manifest: str | Path,
    output_parent: str | Path,
    variants: object,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    snapshot = build_thumbnail_snapshot(project_manifest)
    project_root = Path(str(snapshot["projectFolder"])).resolve(strict=True)
    parent = Path(output_parent).resolve(strict=False)
    if parent == project_root or parent.is_relative_to(project_root) or project_root.is_relative_to(parent):
        raise ThumbnailCandidateError("Candidate output must be external to the Album Project")
    normalized = _validate_variants(variants)
    identity = {"schemaVersion": 1, "snapshot": snapshot, "variants": normalized}
    fingerprint = hashlib.sha256(_canonical(identity)).hexdigest()
    destination = parent / f"render-{fingerprint[:20]}"
    if destination.exists():
        return _validate_existing(destination, fingerprint)
    staging = parent / f".render-{fingerprint[:20]}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    process = None
    try:
        if cancelled and cancelled():
            raise ThumbnailCandidateError("Thumbnail candidate render cancelled")
        _validate_snapshot_current(project_root, snapshot)
        _validate_renderer_current(snapshot)
        input_path = staging / "render-input.json"
        atomic_write_json(input_path, {"schemaVersion": 1, "snapshot": snapshot, "variants": normalized, "outputDirectory": str(staging), "frame": 0})
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen([_node_path(), str(NODE_SCRIPT), str(input_path)], cwd=str(NODE_SCRIPT.parent.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags)
        while process.poll() is None:
            if cancelled and cancelled():
                _terminate_process_tree(process)
                raise ThumbnailCandidateError("Thumbnail candidate render cancelled")
            time.sleep(0.05)
        output = process.stdout.read() if process.stdout else ""
        if process.returncode != 0:
            raise ThumbnailCandidateError("Shared thumbnail renderer failed: " + (output.strip().splitlines()[-1] if output.strip() else f"exit {process.returncode}"))
        _validate_snapshot_current(project_root, snapshot)
        _validate_renderer_current(snapshot)
        outputs = []
        for slot in SLOTS:
            path = staging / f"{slot}.png"
            dimensions = _png_dimensions(path)
            if dimensions != (1280, 720) or path.stat().st_size > 2 * 1024 * 1024:
                raise ThumbnailCandidateError(f"Thumbnail {slot} failed PNG constraints")
            outputs.append({"slot": slot, "filename": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path), "width": dimensions[0], "height": dimensions[1]})
        manifest = {"schemaVersion": 1, "renderFingerprint": fingerprint, "snapshot": snapshot, "variants": normalized, "outputs": outputs, "createdAt": utc_now(), "youtubeMutated": False, "imageGenUsed": False}
        atomic_write_json(staging / "thumbnail-candidates.json", manifest)
        input_path.unlink()
        parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, destination)
        return manifest | {"rootPath": str(destination), "manifestPath": str(destination / "thumbnail-candidates.json")}
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render deterministic A/B/C thumbnail candidates.")
    parser.add_argument("--project-manifest", required=True)
    parser.add_argument("--output-parent", required=True)
    parser.add_argument("--variants-json", required=True)
    args = parser.parse_args(argv)
    try:
        variants = json.loads(Path(args.variants_json).read_text(encoding="utf-8"))
        if isinstance(variants, dict):
            variants = variants.get("variants")
        result = render_thumbnail_candidates(args.project_manifest, args.output_parent, variants)
    except (OSError, json.JSONDecodeError, ThumbnailCandidateError) as exc:
        parser.exit(2, f"thumbnail candidate render failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
