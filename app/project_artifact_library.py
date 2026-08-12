from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .json_store import atomic_write_json, read_json
from .projects import ProjectError, fingerprint_file, normalized_path


LIBRARY_SCHEMA_VERSION = 1
LAYOUT_VERSION = 1
MIGRATION_VERSION = 1
DEFAULT_RESUMABLE_RETENTION_DAYS = 7
PROTECTED = "Protected"
SAFE_TEMPORARY = "Safe temporary"
REVIEW_REQUIRED = "Review required"
RELEASE_STATES = {"Approved", "Ready for review", "Superseded", "Needs fix", "Rejected"}
_GLOB_CHARS = set("*?[]{}")
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACE_RUN = re.compile(r"\s+")


class ArtifactLibraryError(ProjectError):
    """The artifact library refuses an unsafe, incomplete or ambiguous operation."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return fingerprint_file(path)


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_CLEANUP_FINGERPRINT_FIELDS = (
    "schemaVersion", "planType", "projectFolder", "inventoryFingerprint", "targets",
    "reclaimableBytes", "unverifiedSafeBytes", "unverifiedSafeCount", "prunableDirectories", "verifiedHashes",
)
_MIGRATION_ID_FIELDS = (
    "schemaVersion", "planType", "projectFolder", "migrationVersion",
    "sourceProjectManifestSha256", "mappings", "releases", "legacyManifests",
    "currentTechnicalId", "currentDestinationFolder", "pathChecks", "bytes",
)
_MIGRATION_FINGERPRINT_FIELDS = (
    "schemaVersion", "planType", "status", "projectFolder", "migrationVersion",
    "migrationId", "sourceProjectManifestSha256", "mappings", "releases",
    "legacyManifests", "currentTechnicalId", "currentDestinationFolder", "pathChecks", "bytes",
)


def _canonical_fingerprint(plan: dict[str, Any], fields: tuple[str, ...]) -> str:
    if not isinstance(plan, dict) or any(field not in plan for field in fields):
        raise ArtifactLibraryError("The plan is incomplete and has no valid canonical fingerprint.")
    return _digest({field: plan[field] for field in fields})


def _inventory_fingerprint(artifacts: list[dict[str, Any]]) -> str:
    return _digest([{"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]} for item in artifacts])


def cleanup_plan_file_sha256(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key != "planFileSha256"}
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).replace("\n", os.linesep) + os.linesep).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def _release_state(manifest: dict[str, Any]) -> str:
    raw = str(manifest.get("releaseState") or manifest.get("reviewStatus") or "Ready for review").strip().casefold()
    aliases = {"needs-fix": "Needs fix", "needs fix": "Needs fix", "approved": "Approved", "ready": "Ready for review", "ready for review": "Ready for review", "superseded": "Superseded", "rejected": "Rejected"}
    state = aliases.get(raw, "Ready for review")
    return state if state in RELEASE_STATES else "Ready for review"


def sanitize_human_component(value: str, *, fallback: str = "Untitled", limit: int = 96) -> str:
    candidate = _INVALID_NAME.sub("-", str(value or "").strip())
    candidate = _SPACE_RUN.sub(" ", candidate)
    candidate = candidate.strip(" .")
    if not candidate:
        candidate = fallback
    if candidate.casefold() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        candidate = f"{candidate} Project"
    return candidate[:limit].rstrip(" .") or fallback


def human_media_filename(*, artist: str, album: str, state: str = "Approved", note: str | None = None) -> str:
    if not str(artist or "").strip() or not str(album or "").strip():
        raise ArtifactLibraryError("Human media naming requires configured artist and album metadata.")
    # Review notes belong in the manifest.  They are deliberately not part of a
    # path because they are verbose, mutable and can contain unsafe punctuation.
    suffix = "Needs Fix" if state == "Needs fix" else "Full Album Instrumental"
    return f"{sanitize_human_component(artist, fallback='Artist')} - {sanitize_human_component(album, fallback='Album')} - {suffix}.mp4"


def human_release_folder_name(*, date: str, state: str, album: str, note: str | None = None, purpose: str | None = None, collision_index: int | None = None) -> str:
    safe_date = re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or "")
    date_value = date if safe_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    label = sanitize_human_component(purpose or "Full Album Instrumental", fallback="Full Album Instrumental")
    base = f"{date_value} - {state} - {label}"
    if collision_index and collision_index > 1:
        base += f" ({collision_index})"
    return sanitize_human_component(base, fallback=f"{date_value} - {state}", limit=150)


def _assert_no_glob(value: str) -> None:
    if any(character in value for character in _GLOB_CHARS):
        raise ArtifactLibraryError("Glob patterns are not valid artifact paths.")


def _assert_no_link_escape(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ArtifactLibraryError("Artifact path escapes the Project Folder.") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.exists() and (cursor.is_symlink() or _is_junction(cursor)):
            raise ArtifactLibraryError("Symlink/junction paths are not eligible for artifact operations.")
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ArtifactLibraryError("Resolved artifact path escapes the Project Folder.")


def _safe_relative(root: Path, value: str | Path, *, allow_missing: bool = False) -> Path:
    raw = str(value)
    _assert_no_glob(raw)
    candidate = Path(raw)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ArtifactLibraryError("Artifact paths must be relative and cannot traverse parent folders.")
    root = root.resolve(strict=True)
    path = root / candidate
    if not allow_missing and not path.exists():
        raise ArtifactLibraryError(f"Registered artifact is missing: {candidate.as_posix()}")
    _assert_no_link_escape(root, path)
    return path.resolve(strict=False)


def _relative(root: Path, path: Path) -> str:
    root = root.resolve(strict=True)
    _assert_no_link_escape(root, path)
    return path.resolve(strict=False).relative_to(root).as_posix()


def _manifest_date(manifest: dict[str, Any]) -> str:
    value = str(manifest.get("approvedAt") or manifest.get("createdAt") or "")[:10]
    return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else datetime.now(timezone.utc).strftime("%Y-%m-%d")


_JOB_ACTIVE_STATES = {"queued", "running", "cancelling", "stopping", "resumable", "paused"}
_JOB_COMPLETE_STATES = {"complete", "completed", "ready"}
_JOB_FAILED_STATES = {"failed", "error", "cancelled", "canceled"}
_JOB_DURABLE_FILES = {"status.json", "input.json", "render-manifest.json", "manifest.json", "summary.json", "audit.json", "audit.jsonl"}
_JOB_CACHE_PARTS = {
    "cache", "cache_data", "gpucache", "grshadercache", "shadercache", "dawngraphitecache",
    "dawnwebgpucache", "component_crx_cache", "extensions_crx_cache", "crashpad", "browser-cache",
    "browser_cache", "chrome-cache", "chrome_cache", "profile-cache", "profile_cache",
}
_JOB_STAGING_PARTS = {"staging", "frames", "chunks", "synthetic", "preview", "previews", "output", "outputs"}
_NON_PRUNABLE_DIRECTORIES = {
    ".stem-comparison",
    ".stem-comparison/jobs",
    "video",
    "video/releases",
    "video/proofs",
    "video/assets",
}


class ProjectArtifactLibrary:
    """Single seam for durable artifact inventory, release resolution and safe mutation."""

    def __init__(self, project_root: str | Path):
        self.root = normalized_path(project_root)
        if not self.root.is_dir():
            raise ArtifactLibraryError("The Project Folder does not exist.")
        self.manifest_path = self.root / "project.json"
        if not self.manifest_path.is_file():
            raise ArtifactLibraryError("The Project manifest is missing.")
        _assert_no_link_escape(self.root, self.manifest_path)

    def _project(self) -> dict[str, Any]:
        project = read_json(self.manifest_path, None)
        if not isinstance(project, dict):
            raise ArtifactLibraryError("The Project manifest is unreadable.")
        return project

    def _video_config(self) -> dict[str, Any]:
        value = read_json(self.root / "video" / "config.json", {})
        return value if isinstance(value, dict) else {}

    def _package_manifests(self) -> list[tuple[Path, dict[str, Any]]]:
        result: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted((self.root / "video" / "packages").glob("*/manifest.json"), key=lambda item: item.as_posix().casefold()):
            value = read_json(path, None)
            if isinstance(value, dict):
                result.append((path, value))
        return result

    def _release_manifests(self) -> list[tuple[Path, dict[str, Any]]]:
        result: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted((self.root / "video" / "releases").glob("*/manifest.json"), key=lambda item: item.as_posix().casefold()):
            value = read_json(path, None)
            if isinstance(value, dict):
                result.append((path, value))
        return result

    def resolve_current_release(self) -> dict[str, Any]:
        project = self._project()
        pointer = project.get("artifactLibrary", {}).get("currentRelease") if isinstance(project.get("artifactLibrary"), dict) else None
        if isinstance(pointer, dict) and pointer.get("manifestPath"):
            manifest_path = _safe_relative(self.root, str(pointer["manifestPath"]))
            manifest = read_json(manifest_path, None)
            if not isinstance(manifest, dict) or manifest.get("releaseId") != pointer.get("releaseId"):
                raise ArtifactLibraryError("The current release pointer is incomplete or corrupt.")
            if _release_state(manifest) != "Approved":
                raise ArtifactLibraryError("The current release pointer does not reference an Approved release.")
            return {"layout": "new", "manifestPath": _relative(self.root, manifest_path), "folder": _relative(self.root, manifest_path.parent), "manifest": manifest, "releaseId": manifest.get("releaseId")}

        candidates = [(path, value) for path, value in self._release_manifests() + self._package_manifests() if _release_state(value) == "Approved" and value.get("current") is True]
        if len(candidates) > 1:
            raise ArtifactLibraryError("Multiple current releases are registered; refusing to guess.")
        if not candidates:
            approved = [(path, value) for path, value in self._release_manifests() + self._package_manifests() if _release_state(value) == "Approved" and value.get("kind") in {"real-album-video-package", "real-video-release"}]
            if len(approved) == 1:
                candidates = approved
        if len(candidates) != 1:
            raise ArtifactLibraryError("No unambiguous current Approved release is registered.")
        path, manifest = candidates[0]
        return {"layout": "legacy" if "packages" in path.parts else "new", "manifestPath": _relative(self.root, path), "folder": _relative(self.root, path.parent), "manifest": manifest, "releaseId": manifest.get("releaseId") or manifest.get("packageId")}

    def _registered_metadata(self) -> dict[Path, dict[str, Any]]:
        records: dict[Path, dict[str, Any]] = {}
        for manifest_path, manifest in self._package_manifests() + self._release_manifests():
            folder = manifest_path.parent
            package_state = _release_state(manifest)
            package_kind = str(manifest.get("kind") or "")
            for key, record in (manifest.get("artifacts") or {}).items() if isinstance(manifest.get("artifacts"), dict) else []:
                if not isinstance(record, dict) or not record.get("path"):
                    continue
                try:
                    path = _safe_relative(self.root, _relative(self.root, folder / str(record["path"])))
                except ArtifactLibraryError:
                    continue
                records[path] = {"manifestPath": _relative(self.root, manifest_path), "role": key, "state": package_state, "kind": package_kind, "declaredSha256": record.get("sha256"), "current": manifest.get("current") is True}
            selection = manifest.get("selectionSnapshot") if isinstance(manifest.get("selectionSnapshot"), list) else []
            for item in selection:
                if not isinstance(item, dict) or not item.get("finalPath"):
                    continue
                try:
                    path = _safe_relative(self.root, str(item["finalPath"]))
                except ArtifactLibraryError:
                    continue
                records[path] = {"manifestPath": _relative(self.root, manifest_path), "role": "Final Instrumental", "state": "Approved", "kind": "selection", "declaredSha256": item.get("fileFingerprint"), "current": manifest.get("current") is True}
        for path in self.root.rglob("render-manifest.json"):
            manifest = read_json(path, None)
            if not isinstance(manifest, dict):
                continue
            output = str(manifest.get("outputPath") or "")
            if output:
                try:
                    output_path = _safe_relative(self.root, output)
                    records[output_path] = {"manifestPath": _relative(self.root, path), "role": "render output", "state": _release_state(manifest), "kind": str(manifest.get("kind") or "render"), "declaredSha256": (manifest.get("validation") or {}).get("sha256"), "current": False, "renderStatus": str(manifest.get("status") or "complete")}
                except ArtifactLibraryError:
                    pass
        return records

    def _current_folder(self) -> str | None:
        try:
            return str(self.resolve_current_release().get("folder"))
        except ArtifactLibraryError:
            return None

    def _all_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if path.is_file() and path.name not in {".project.lock"} and not path.name.endswith(".tmp"):
                files.append(path)
        return sorted(files, key=lambda item: item.as_posix().casefold())

    def _video_job_metadata(self) -> dict[str, dict[str, Any]]:
        """Read each video-job status once and fail closed on ambiguity."""
        jobs_root = self.root / ".stem-comparison" / "video-jobs"
        result: dict[str, dict[str, Any]] = {}
        if not jobs_root.is_dir():
            return result
        durable_promotions: set[tuple[str, int]] = set()
        for manifest_root in (self.root / "video" / "releases", self.root / "video" / "proofs", self.root / "video" / "renders"):
            if not manifest_root.is_dir():
                continue
            for manifest_path in manifest_root.rglob("*.json"):
                try:
                    manifest = read_json(manifest_path, None)
                except (OSError, TypeError, ValueError):
                    manifest = None
                if not isinstance(manifest, dict):
                    continue
                validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
                validation_sha = str(validation.get("sha256") or "")
                validation_bytes = validation.get("bytes")
                if validation_sha and isinstance(validation_bytes, int):
                    durable_promotions.add((validation_sha, validation_bytes))
                artifact = manifest.get("artifacts", {}).get("albumVideo", {}) if isinstance(manifest.get("artifacts"), dict) else {}
                artifact_sha = str(artifact.get("sha256") or manifest.get("renderSha256") or "") if isinstance(artifact, dict) else ""
                artifact_bytes = artifact.get("bytes") if isinstance(artifact, dict) else None
                if artifact_sha and isinstance(artifact_bytes, int):
                    durable_promotions.add((artifact_sha, artifact_bytes))
        for job_root in sorted((path for path in jobs_root.iterdir() if path.is_dir()), key=lambda item: item.name.casefold()):
            status_path = job_root / "status.json"
            metadata: dict[str, Any] = {
                "jobId": job_root.name,
                "jobState": "unknown",
                "jobStatus": None,
                "jobStatusPath": _relative(self.root, status_path),
                "promotionVerified": False,
            }
            try:
                raw = read_json(status_path, None)
            except (OSError, TypeError, ValueError):
                raw = None
            if not isinstance(raw, dict):
                metadata["jobReason"] = "Job status is absent or corrupt; refusing to infer cleanup safety."
                result[job_root.name] = metadata
                continue
            status = str(raw.get("status") or "").casefold()
            stage = str(raw.get("stage") or "").casefold()
            metadata["jobStatus"] = status or None
            metadata["jobStage"] = stage or None
            contradictory = (
                not status
                or not stage
                or (status in _JOB_COMPLETE_STATES and stage in _JOB_FAILED_STATES)
                or (status in _JOB_FAILED_STATES and stage in _JOB_COMPLETE_STATES)
                or (status in _JOB_ACTIVE_STATES and stage in _JOB_FAILED_STATES)
            )
            if contradictory:
                metadata["jobState"] = "unknown"
                metadata["jobReason"] = "Job status and stage are absent, corrupt or contradictory; refusing to infer cleanup safety."
                result[job_root.name] = metadata
                continue
            if status in _JOB_ACTIVE_STATES:
                metadata["jobState"] = "active"
                metadata["jobReason"] = "Active or resumable job state must retain continuation artifacts."
            elif status == "interrupted":
                resumable = bool(raw.get("resumable") or raw.get("resumeToken") or raw.get("resumePath") or stage in {"resumable", "paused"})
                metadata["jobState"] = "resumable" if resumable else "interrupted"
                metadata["jobReason"] = "Interrupted job is resumable and protected." if resumable else "Interrupted job remains Review required during retention."
            elif status in _JOB_COMPLETE_STATES:
                metadata["jobState"] = "complete"
                metadata["jobReason"] = "Completed job has no verifiable promoted output."
            elif status in _JOB_FAILED_STATES:
                metadata["jobState"] = "failed"
                metadata["jobReason"] = "Failed job remains Review required during retention."
            else:
                metadata["jobState"] = "unknown"
                metadata["jobReason"] = "Unknown job state; refusing to infer cleanup safety."

            if metadata["jobState"] == "complete":
                validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {}
                checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
                checks_valid = bool(checks) and all(value is True for value in checks.values())
                declared_bytes = validation.get("bytes")
                declared_sha = str(validation.get("sha256") or "")
                promoted_raw = str(raw.get("promotedPath") or "")
                promoted: Path | None = None
                if promoted_raw:
                    candidate = Path(promoted_raw)
                    promoted = candidate if candidate.is_absolute() else self.root / candidate
                    try:
                        promoted = promoted.resolve(strict=False)
                        _assert_no_link_escape(self.root, promoted)
                    except (ArtifactLibraryError, OSError):
                        promoted = None
                verified = bool(
                    checks_valid
                    and promoted
                    and promoted.is_file()
                    and declared_sha
                    and isinstance(declared_bytes, int)
                    and promoted.stat().st_size == declared_bytes
                )
                if not verified and checks_valid and declared_sha and isinstance(declared_bytes, int) and (declared_sha, declared_bytes) in durable_promotions:
                    verified = True
                if not verified and checks_valid and declared_sha and isinstance(declared_bytes, int):
                    try:
                        current_release = self.resolve_current_release()
                        current_manifest = current_release.get("manifest") if isinstance(current_release, dict) else None
                        current_artifact = current_manifest.get("artifacts", {}).get("albumVideo", {}) if isinstance(current_manifest, dict) else {}
                        current_sha = str(current_manifest.get("renderSha256") or current_artifact.get("sha256") or "") if isinstance(current_manifest, dict) else ""
                        current_bytes = current_artifact.get("bytes") if isinstance(current_artifact, dict) else None
                        verified = bool(
                            isinstance(current_manifest, dict)
                            and current_manifest.get("current") is True
                            and current_manifest.get("reviewStatus") == "approved"
                            and current_sha == declared_sha
                            and current_bytes == declared_bytes
                        )
                    except ArtifactLibraryError:
                        verified = False
                metadata["promotionVerified"] = verified
                metadata["promotedPath"] = _relative(self.root, promoted) if promoted else None
                if verified:
                    metadata["jobReason"] = "Completed job has a promoted output with passing validation evidence."
                else:
                    metadata["jobReason"] = "Completed job has no verifiable promoted output; non-durable artifacts require review."
            result[job_root.name] = metadata
        return result

    @staticmethod
    def _video_job_file_role(relative: str) -> str:
        parts = Path(relative).parts
        job_parts = parts[3:]
        if len(job_parts) == 1 and job_parts[0].casefold() in _JOB_DURABLE_FILES:
            return "durable-summary"
        lowered = {part.casefold() for part in job_parts[:-1]}
        if any(part in _JOB_CACHE_PARTS or "cache" in part or "profile" in part for part in lowered):
            return "regenerable-cache"
        if any(part in _JOB_STAGING_PARTS for part in lowered):
            return "staging"
        return "job-artifact"

    def _classify(self, relative: str, metadata: dict[str, Any], current_folder: str | None) -> tuple[str, str]:
        path = Path(relative)
        parts = path.parts
        if relative == "project.json" or parts[:1] in (("final",), ("outputs",)):
            return PROTECTED, "Project manifest, Final Instrumental or current Output source."
        if parts[:3] == ("video", "assets",) or parts[:2] == ("video", "assets"):
            return PROTECTED, "Active snapshotted Video asset."
        if current_folder and (relative == current_folder or relative.startswith(current_folder.rstrip("/") + "/")):
            return PROTECTED, "Current Approved release."
        if parts[:2] == ("video", "releases"):
            return REVIEW_REQUIRED, "Non-current release media requires explicit review."
        if parts[:2] == ("video", "proofs"):
            return REVIEW_REQUIRED, "Proof history is retained for review."
        if parts[:2] == ("video", "packages"):
            if metadata.get("kind", "").startswith("synthetic"):
                return SAFE_TEMPORARY, "Synthetic package inside a real Project Folder."
            if metadata.get("current"):
                return PROTECTED, "Legacy package is the current release source."
            return REVIEW_REQUIRED, "Legacy historical or Needs-fix package."
        if parts[:2] == ("video", "renders"):
            if metadata.get("declaredSha256") and metadata.get("current") is False and metadata.get("renderStatus") in {"complete", "completed", "ready"}:
                try:
                    current = self.resolve_current_release()
                    current_video = (current.get("manifest", {}).get("artifacts", {}).get("albumVideo", {}) if isinstance(current.get("manifest"), dict) else {}).get("sha256")
                    if current_video and metadata.get("declaredSha256") == current_video:
                        return SAFE_TEMPORARY, "Validated render copy is byte-identical to the current release."
                except ArtifactLibraryError:
                    pass
            if metadata.get("renderStatus") in {"complete", "completed", "ready"}:
                return SAFE_TEMPORARY, "Completed render staging is regenerable after release validation."
            return REVIEW_REQUIRED, "Render registration is incomplete or ambiguous."
        if parts[:2] == ("video", "previews"):
            return SAFE_TEMPORARY, "Preview media is disposable and regenerable."
        if parts[:1] == (".stem-comparison",):
            if parts[1:2] == ("jobs",):
                return PROTECTED, "Durable job summary or audit evidence."
            if parts[1:2] == ("video-jobs",) and len(parts) >= 4:
                job_state = metadata.get("jobState", "unknown")
                role = metadata.get("jobRole") or self._video_job_file_role(relative)
                promotion_verified = metadata.get("promotionVerified") is True
                if job_state in {"failed", "interrupted", "unknown"}:
                    return REVIEW_REQUIRED, metadata.get("jobReason") or "Failed, interrupted or ambiguous job remains Review required during retention."
                if job_state in {"active", "resumable"}:
                    if role == "regenerable-cache":
                        return SAFE_TEMPORARY, "Active job browser/cache data is regenerable; continuation state remains protected."
                    return PROTECTED, "Active/resumable job continuation artifact."
                if job_state == "complete" and promotion_verified:
                    if role == "durable-summary":
                        return PROTECTED, "Durable summary for a completed job with a verified promoted output."
                    return SAFE_TEMPORARY, "Completed job output is promoted and verified; non-durable job artifact is regenerable."
                if job_state == "complete":
                    if role == "durable-summary":
                        return PROTECTED, "Durable summary retained while completed job promotion remains unverified."
                    return REVIEW_REQUIRED, "Completed job has no verifiable promotion; artifact may be needed for recovery or diagnosis."
                return REVIEW_REQUIRED, "Unknown job state; refusing to infer cleanup safety."
            if parts[1:2] == ("work",):
                if metadata.get("active"):
                    return PROTECTED, "Active or resumable staging must not be cleaned."
                if metadata.get("completed"):
                    return SAFE_TEMPORARY, "Completed validated staging can be regenerated."
                return REVIEW_REQUIRED, "Failed or interrupted work is retained for review/retention."
            if parts[1:2] == ("cache",):
                return SAFE_TEMPORARY, "Hidden derived cache is regenerable and not a source artifact."
            if parts[1:2] == ("audit",):
                return PROTECTED, "Append-only artifact audit evidence."
        if metadata:
            return REVIEW_REQUIRED, "Registered outside a recognized durable role."
        return REVIEW_REQUIRED, "Unregistered file; refusing to infer that it is disposable."

    @staticmethod
    def _area(relative: str) -> str:
        parts = Path(relative).parts
        if parts[:1] in (("final",), ("outputs",)):
            return "Instrumentals"
        if parts[:2] == ("video", "releases"):
            return "Video releases"
        if parts[:2] == ("video", "proofs") or parts[:2] == ("video", "packages"):
            return "Video proofs / legacy"
        if parts[:2] == ("video", "assets"):
            return "Video assets"
        if parts[:2] == ("video", "renders") or parts[:2] == ("video", "previews"):
            return "Video staging"
        if parts[:1] == (".stem-comparison",):
            return "Hidden work / cache"
        return "Project / other"

    def _directory_contents(self, directory: Path) -> tuple[set[str], set[str]]:
        files: set[str] = set()
        directories: set[str] = set()

        def visit(current: Path) -> None:
            try:
                entries = list(current.iterdir())
            except OSError as exc:
                raise ArtifactLibraryError(f"Could not inspect cleanup directory: {_relative(self.root, current)}") from exc
            for entry in entries:
                if entry.is_symlink() or _is_junction(entry):
                    raise ArtifactLibraryError(f"Symlink/junction paths are not eligible for cleanup pruning: {_relative(self.root, entry)}")
                relative = _relative(self.root, entry)
                if entry.is_dir():
                    directories.add(relative)
                    visit(entry)
                elif entry.is_file():
                    files.add(relative)
                else:
                    raise ArtifactLibraryError(f"Unsupported filesystem entry is not eligible for cleanup pruning: {relative}")

        visit(directory)
        return files, directories

    @staticmethod
    def _is_non_prunable_directory(relative: str) -> bool:
        return relative in _NON_PRUNABLE_DIRECTORIES

    def _symlink_paths(self) -> set[str]:
        links: set[str] = set()
        for current, directories, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in directories:
                entry = current_path / name
                if entry.is_symlink() or _is_junction(entry):
                    links.add(_relative(self.root, entry))
                else:
                    kept_directories.append(name)
            directories[:] = kept_directories
            for name in files:
                entry = current_path / name
                if entry.is_symlink() or _is_junction(entry):
                    links.add(_relative(self.root, entry))
        return links

    def _derive_prunable_directories(self, inventory: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]:
        target_paths = {str(item["path"]) for item in targets}
        protected_or_review = {
            str(item["path"])
            for item in inventory.get("artifacts", [])
            if item.get("category") in {PROTECTED, REVIEW_REQUIRED}
        }
        candidates: set[str] = set()
        for target in targets:
            target_path = _safe_relative(self.root, str(target["path"]), allow_missing=False)
            parent = target_path.parent
            while parent != self.root:
                relative = _relative(self.root, parent)
                if self._is_non_prunable_directory(relative):
                    break
                _safe_relative(self.root, relative, allow_missing=False)
                candidates.add(relative)
                parent = parent.parent

        def add_descendant_directories(directory: Path) -> None:
            if directory.is_symlink() or _is_junction(directory):
                raise ArtifactLibraryError(f"Symlink/junction paths are not eligible for cleanup pruning: {_relative(self.root, directory)}")
            for descendant in directory.rglob("*"):
                if descendant.is_symlink() or _is_junction(descendant):
                    raise ArtifactLibraryError(f"Symlink/junction paths are not eligible for cleanup pruning: {_relative(self.root, descendant)}")
                if descendant.is_dir():
                    relative = _relative(self.root, descendant)
                    if not self._is_non_prunable_directory(relative):
                        _safe_relative(self.root, relative, allow_missing=False)
                        candidates.add(relative)

        for relative in list(candidates):
            add_descendant_directories(_safe_relative(self.root, relative, allow_missing=False))

        for job_id, metadata in self._video_job_metadata().items():
            if metadata.get("jobState") != "complete" or metadata.get("promotionVerified") is not True:
                continue
            job_root = self.root / ".stem-comparison" / "video-jobs" / job_id
            if not job_root.is_dir() or job_root.is_symlink() or _is_junction(job_root):
                continue
            for safe_subtree in job_root.iterdir():
                if safe_subtree.is_symlink() or _is_junction(safe_subtree):
                    raise ArtifactLibraryError(f"Symlink/junction paths are not eligible for cleanup pruning: {_relative(self.root, safe_subtree)}")
                if safe_subtree.is_dir():
                    add_descendant_directories(safe_subtree)

        symlinks = self._symlink_paths()
        prunable: set[str] = set()
        for relative in sorted(candidates, key=lambda item: (-len(Path(item).parts), item.casefold())):
            if self._is_non_prunable_directory(relative):
                continue
            directory = _safe_relative(self.root, relative, allow_missing=False)
            if not directory.is_dir():
                continue
            prefix = relative.rstrip("/") + "/"
            if any(path == relative or path.startswith(prefix) for path in symlinks):
                raise ArtifactLibraryError(f"Symlink/junction paths are not eligible for cleanup pruning: {relative}")
            contains_protected_or_review = any(path == relative or path.startswith(prefix) for path in protected_or_review)
            if contains_protected_or_review:
                continue
            remains = False
            for entry in directory.iterdir():
                entry_relative = _relative(self.root, entry)
                if entry.is_symlink() or _is_junction(entry):
                    raise ArtifactLibraryError(f"Symlink/junction paths are not eligible for cleanup pruning: {entry_relative}")
                if entry.is_file():
                    if entry_relative in target_paths:
                        continue
                    remains = True
                    break
                if entry.is_dir() and entry_relative not in prunable:
                    remains = True
                    break
            if not remains:
                prunable.add(relative)
        blocked_subtrees = {
            relative
            for relative in candidates
            if not (Path(relative).parts[:2] == (".stem-comparison", "video-jobs") and len(Path(relative).parts) == 3)
            and any(Path(path).parent.as_posix() == relative for path in protected_or_review)
        }
        prunable = {
            relative
            for relative in prunable
            if not any(relative == blocked or relative.startswith(blocked.rstrip("/") + "/") for blocked in blocked_subtrees)
        }
        return sorted(prunable, key=lambda item: (-len(Path(item).parts), item.casefold()))

    def inventory(self, *, verify_hashes: bool = False) -> dict[str, Any]:
        registered = self._registered_metadata()
        current_folder = self._current_folder()
        video_jobs = self._video_job_metadata()
        records: list[dict[str, Any]] = []
        for path in self._all_files():
            relative = _relative(self.root, path)
            metadata = registered.get(path.resolve(strict=False), {})
            active = False
            completed = False
            if relative.startswith(".stem-comparison/work/"):
                status_files = list(path.parent.rglob("status.json")) if path.parent.is_dir() else []
                statuses = [read_json(item, {}) for item in status_files]
                active = any(_active_job_status(item.get("status")) for item in statuses if isinstance(item, dict))
                completed = any(str(item.get("status") or "").casefold() in {"complete", "completed", "ready"} for item in statuses if isinstance(item, dict))
            job_parts = Path(relative).parts
            if job_parts[:2] == (".stem-comparison", "video-jobs") and len(job_parts) >= 4:
                job = video_jobs.get(job_parts[2], {"jobId": job_parts[2], "jobState": "unknown", "jobReason": "Job root is missing or ambiguous; refusing to infer cleanup safety."})
                metadata = {**metadata, **job, "jobRole": self._video_job_file_role(relative)}
            metadata = {**metadata, "active": active, "completed": completed}
            category, reason = self._classify(relative, metadata, current_folder)
            declared = metadata.get("declaredSha256")
            if verify_hashes:
                sha = _sha256(path)
                hash_source = "verified"
            elif declared:
                sha = str(declared)
                hash_source = "manifest"
            else:
                # A normal Storage inventory must remain a metadata-only read. Hashing
                # every unregistered byte of a real Project Folder would turn opening
                # the view into a multi-gigabyte background job. Cleanup plans require
                # verified hashes separately before they can become destructive.
                sha = None
                hash_source = "unverified"
            records.append({"artifactId": f"path:{_digest(relative)[:16]}", "path": relative, "area": self._area(relative), "bytes": path.stat().st_size, "sha256": sha, "hashSource": hash_source, "category": category, "reason": reason, "registered": bool(metadata), "manifestPath": metadata.get("manifestPath"), "role": metadata.get("role") or metadata.get("jobRole"), "jobId": metadata.get("jobId"), "jobStatus": metadata.get("jobStatus"), "jobState": metadata.get("jobState"), "promotionVerified": metadata.get("promotionVerified")})
        inventory_fingerprint = _inventory_fingerprint(records)
        totals = {category: sum(item["bytes"] for item in records if item["category"] == category) for category in (PROTECTED, SAFE_TEMPORARY, REVIEW_REQUIRED)}
        return {"schemaVersion": LIBRARY_SCHEMA_VERSION, "layoutVersion": LAYOUT_VERSION, "projectFolder": str(self.root), "currentRelease": self.resolve_current_release() if current_folder else None, "inventoryFingerprint": inventory_fingerprint, "artifacts": records, "totals": totals, "reclaimableBytes": totals[SAFE_TEMPORARY], "verifiedHashes": verify_hashes, "generatedAt": _utc_now()}

    def plan_cleanup(self, *, verify_hashes: bool = False, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
        inventory = inventory or self.inventory(verify_hashes=verify_hashes)
        if not isinstance(inventory, dict) or inventory.get("projectFolder") != str(self.root):
            raise ArtifactLibraryError("Cleanup inventory does not belong to the Project Folder.")
        if verify_hashes and inventory.get("verifiedHashes") is not True:
            raise ArtifactLibraryError("Cleanup planning requires a hash-verified inventory.")
        safe = [item for item in inventory["artifacts"] if item["category"] == SAFE_TEMPORARY]
        candidates = [item for item in safe if item.get("sha256")]
        unverified = [item for item in safe if not item.get("sha256")]
        plan = {"schemaVersion": LIBRARY_SCHEMA_VERSION, "planType": "cleanup", "projectFolder": str(self.root), "inventoryFingerprint": inventory["inventoryFingerprint"], "targets": candidates, "reclaimableBytes": sum(item["bytes"] for item in candidates), "unverifiedSafeBytes": sum(item["bytes"] for item in unverified), "unverifiedSafeCount": len(unverified), "prunableDirectories": self._derive_prunable_directories(inventory, candidates), "verifiedHashes": inventory.get("verifiedHashes") is True, "createdAt": _utc_now()}
        plan["planFingerprint"] = _canonical_fingerprint(plan, _CLEANUP_FINGERPRINT_FIELDS)
        return plan

    def _prune_empty_directories(self, directories: list[str]) -> tuple[list[str], list[dict[str, str]]]:
        removed: list[str] = []
        skipped: list[dict[str, str]] = []
        for relative in sorted(directories, key=lambda item: (-len(Path(item).parts), item.casefold())):
            try:
                if self._is_non_prunable_directory(relative):
                    skipped.append({"path": relative, "reason": "durable-root"})
                    continue
                directory = _safe_relative(self.root, relative, allow_missing=True)
                if not directory.exists():
                    skipped.append({"path": relative, "reason": "already-absent"})
                    continue
                if not directory.is_dir() or directory.is_symlink() or _is_junction(directory):
                    skipped.append({"path": relative, "reason": "symlink-or-not-directory"})
                    continue
                entries = list(directory.iterdir())
                if entries:
                    empty_unplanned_descendants = all(
                        entry.is_dir()
                        and not entry.is_symlink()
                        and not _is_junction(entry)
                        and not any(entry.iterdir())
                        and _relative(self.root, entry) not in directories
                        for entry in entries
                    )
                    skipped.append({"path": relative, "reason": "unplanned-empty-descendant" if empty_unplanned_descendants else "not-empty-at-apply"})
                    continue
                directory.rmdir()
                removed.append(relative)
            except (ArtifactLibraryError, OSError) as exc:
                skipped.append({"path": relative, "reason": str(exc) or "unsafe-or-raced"})
        return removed, skipped

    def apply_cleanup(self, plan: dict[str, Any], *, confirm_fingerprint: str | None = None, confirm_plan_file_sha256: str | None = None) -> dict[str, Any]:
        if not isinstance(plan, dict) or plan.get("planType") != "cleanup":
            raise ArtifactLibraryError("The cleanup plan is unreadable.")
        if not isinstance(confirm_fingerprint, str) or not confirm_fingerprint.strip():
            raise ArtifactLibraryError("Cleanup confirmation fingerprint is required.")
        if not isinstance(confirm_plan_file_sha256, str) or not confirm_plan_file_sha256.strip():
            raise ArtifactLibraryError("Cleanup plan file SHA-256 confirmation is required.")
        approved_fingerprint = plan.get("planFingerprint")
        if not isinstance(approved_fingerprint, str) or not approved_fingerprint.strip():
            raise ArtifactLibraryError("The cleanup plan has no approved fingerprint.")
        canonical_fingerprint = _canonical_fingerprint(plan, _CLEANUP_FINGERPRINT_FIELDS)
        if canonical_fingerprint != approved_fingerprint:
            raise ArtifactLibraryError("Cleanup plan content changed; its fingerprint is no longer canonical.")
        if confirm_fingerprint != canonical_fingerprint:
            raise ArtifactLibraryError("Cleanup confirmation does not match the approved plan.")
        canonical_file_sha256 = cleanup_plan_file_sha256(plan)
        declared_file_sha256 = plan.get("planFileSha256")
        if declared_file_sha256 is not None and declared_file_sha256 != canonical_file_sha256:
            raise ArtifactLibraryError("Cleanup plan file SHA-256 metadata is not canonical.")
        if confirm_plan_file_sha256 != canonical_file_sha256:
            raise ArtifactLibraryError("Cleanup plan file SHA-256 confirmation does not match the saved plan.")
        verify_targets = bool(plan.get("verifiedHashes")) or any(isinstance(target, dict) and target.get("sha256") for target in plan.get("targets", []))
        current = self.inventory(verify_hashes=verify_targets)
        if current["inventoryFingerprint"] != plan.get("inventoryFingerprint"):
            raise ArtifactLibraryError("Cleanup plan invalidated because the filesystem changed.")
        planned_target_paths = {str(target.get("path")) for target in plan.get("targets", []) if isinstance(target, dict)}
        current_targets = [item for item in current["artifacts"] if item["path"] in planned_target_paths and item["category"] == SAFE_TEMPORARY and item.get("sha256")]
        expected_prunable = self._derive_prunable_directories(current, current_targets)
        if plan.get("prunableDirectories") != expected_prunable:
            raise ArtifactLibraryError("Cleanup plan prunable directories no longer match the verified inventory.")
        current_by_path = {item["path"]: item for item in current["artifacts"]}
        deleted: list[dict[str, Any]] = []
        for target in plan.get("targets", []):
            relative = str(target.get("path") or "") if isinstance(target, dict) else ""
            path = _safe_relative(self.root, relative)
            record = current_by_path.get(relative)
            if not record or record.get("category") != SAFE_TEMPORARY or record.get("sha256") != target.get("sha256"):
                raise ArtifactLibraryError(f"Cleanup target is no longer an unchanged Safe temporary: {relative}")
            if not path.is_file():
                raise ArtifactLibraryError(f"Cleanup target is missing: {relative}")
            path.unlink()
            deleted.append({"path": relative, "bytes": int(target.get("bytes", 0)), "sha256": target.get("sha256"), "category": SAFE_TEMPORARY})
        pruned_directories, skipped_directories = self._prune_empty_directories(plan.get("prunableDirectories", []))
        audit = self.root / ".stem-comparison" / "audit" / "cleanup.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": _utc_now(), "planFingerprint": plan["planFingerprint"], "categories": [SAFE_TEMPORARY], "paths": [item["path"] for item in deleted], "bytes": sum(item["bytes"] for item in deleted), "directories": pruned_directories, "skippedDirectories": skipped_directories, "result": "completed"}, ensure_ascii=False, sort_keys=True) + "\n")
        return {"status": "completed", "planFingerprint": plan["planFingerprint"], "deleted": deleted, "bytes": sum(item["bytes"] for item in deleted), "prunedDirectories": pruned_directories, "skippedDirectories": skipped_directories}

    def _release_metadata(self, package_manifest: dict[str, Any], package_path: Path, *, destination_folder: str, human_folder: str, mappings: list[dict[str, Any]], human_purpose: str | None = None, human_media_name: str | None = None) -> dict[str, Any]:
        project = self._project()
        config = self._video_config()
        artist = str(config.get("artist") or project.get("artist") or "")
        album = str(config.get("album") or project.get("albumName") or "")
        state = _release_state(package_manifest)
        note = str(package_manifest.get("reviewNote") or "").strip() or None
        media_mapping = next((item for item in mappings if item.get("sourceRelative") == f"{_relative(self.root, package_path)}/album-video.mp4"), None)
        release_manifest = deepcopy(package_manifest)
        # The new manifest is staged before the Project pointer switches. Keeping
        # current=false here prevents two layouts from declaring Current during
        # promotion; the finalizer sets the sole approved destination to true.
        release_manifest.update({"schemaVersion": 1, "layoutVersion": LAYOUT_VERSION, "releaseId": f"release:{package_manifest.get('packageId') or _digest(_relative(self.root, package_path))[:16]}", "humanLabel": human_folder, "releaseState": state, "current": False, "legacySource": _relative(self.root, package_path), "migratedAt": _utc_now()})
        artifacts: dict[str, Any] = {}
        for key, record in (package_manifest.get("artifacts") or {}).items() if isinstance(package_manifest.get("artifacts"), dict) else []:
            if not isinstance(record, dict) or not record.get("path"):
                continue
            source_relative = f"{_relative(self.root, package_path)}/{str(record['path']).replace('\\', '/') }"
            mapping = next((item for item in mappings if item.get("sourceRelative") == source_relative), None)
            if mapping:
                artifacts[key] = {**record, "path": Path(mapping["destinationRelative"]).name if Path(mapping["destinationRelative"]).parent == Path(destination_folder) else Path(mapping["destinationRelative"]).relative_to(destination_folder).as_posix()}
        if media_mapping:
            artifacts["albumVideo"] = {**(artifacts.get("albumVideo") or {}), "path": Path(media_mapping["destinationRelative"]).relative_to(destination_folder).as_posix()}
        release_manifest["artifacts"] = artifacts
        release_manifest["provenance"] = {**(package_manifest.get("provenance") or {}), "legacyPackage": _relative(self.root, package_path), "migrationVersion": MIGRATION_VERSION, "technicalId": package_manifest.get("packageId"), "humanPurpose": human_purpose or "Full Album Instrumental"}
        release_manifest["legacyManifest"] = deepcopy(package_manifest)
        if artist and album and "albumVideo" in artifacts:
            release_manifest["humanMediaFilename"] = human_media_name or human_media_filename(artist=artist, album=album, state=state, note=note)
        return release_manifest

    def plan_layout_migration(self) -> dict[str, Any]:
        project = self._project()
        existing = project.get("artifactLibrary") if isinstance(project.get("artifactLibrary"), dict) else {}
        if int(existing.get("layoutVersion", 0) or 0) >= LAYOUT_VERSION and existing.get("currentRelease"):
            return {"schemaVersion": LIBRARY_SCHEMA_VERSION, "planType": "layout-migration", "status": "already_migrated", "projectFolder": str(self.root), "currentRelease": existing.get("currentRelease"), "mappings": [], "reclaimableBytes": 0, "createdAt": _utc_now()}
        packages = self._package_manifests()
        if not packages:
            raise ArtifactLibraryError("No legacy Video Packages are registered for migration.")
        config = self._video_config()
        album = str(config.get("album") or project.get("albumName") or "Album")
        used_names: set[str] = set()
        mappings: list[dict[str, Any]] = []
        release_records: list[dict[str, Any]] = []
        synthetic_sequence = 0
        for manifest_path, manifest in packages:
            package_folder = manifest_path.parent
            state = _release_state(manifest)
            note = str(manifest.get("reviewNote") or "").strip() or None
            is_synthetic = str(manifest.get("kind") or "").startswith("synthetic")
            target_base = "video/proofs" if is_synthetic else "video/releases"
            if is_synthetic:
                synthetic_sequence += 1
                human_purpose = f"Synthetic Export Smoke {synthetic_sequence:02d}"
                human_media_name = f"{human_purpose}.mp4"
            else:
                human_purpose = "Full Album Instrumental"
                human_media_name = human_media_filename(artist=str(config.get("artist") or project.get("artist") or "Artist"), album=album, state=state, note=note)
            base_name = human_release_folder_name(date=_manifest_date(manifest), state=state, album=album, purpose=human_purpose)
            name = base_name
            index = 2
            while name.casefold() in used_names:
                name = human_release_folder_name(date=_manifest_date(manifest), state=state, album=album, purpose=human_purpose, collision_index=index)
                index += 1
            used_names.add(name.casefold())
            destination_folder = Path(target_base) / name
            package_files = sorted(path for path in package_folder.rglob("*") if path.is_file())
            for source_path in package_files:
                source_relative = _relative(self.root, source_path)
                target_name = source_path.name
                if source_path.name.casefold() == "album-video.mp4":
                    target_name = human_media_name
                destination_relative = (destination_folder / target_name).as_posix()
                mappings.append({"sourceRelative": source_relative, "destinationRelative": destination_relative, "bytes": source_path.stat().st_size, "sha256": _sha256(source_path), "role": "release-artifact" if target_name != "manifest.json" else "release-manifest", "technicalId": manifest.get("packageId")})
            release_records.append({"technicalId": manifest.get("packageId"), "humanLabel": name, "humanPurpose": human_purpose, "humanMediaFilename": human_media_name, "state": state, "destinationFolder": destination_folder.as_posix(), "sourceFolder": _relative(self.root, package_folder), "current": bool(manifest.get("current") is True and state == "Approved")})
        current = next((record for record in release_records if record["current"]), None)
        if current is None:
            raise ArtifactLibraryError("Legacy migration has no unambiguous current Approved release.")
        plan = {"schemaVersion": LIBRARY_SCHEMA_VERSION, "planType": "layout-migration", "status": "planned", "projectFolder": str(self.root), "migrationVersion": MIGRATION_VERSION, "sourceProjectManifestSha256": _sha256(self.manifest_path), "mappings": mappings, "releases": release_records, "legacyManifests": {str(manifest.get("packageId")): manifest for _path, manifest in packages}, "currentTechnicalId": current["technicalId"], "currentDestinationFolder": current["destinationFolder"], "createdAt": _utc_now()}
        destination_paths = [str(item["destinationRelative"]) for item in mappings]
        plan["pathChecks"] = {"collisions": sorted({path for path in destination_paths if destination_paths.count(path) > 1}), "maxDestinationPathLength": max((len(str(self.root / path)) for path in destination_paths), default=len(str(self.root))), "excessivePaths": sorted(path for path in destination_paths if len(str(self.root / path)) > 240)}
        plan["bytes"] = sum(item["bytes"] for item in mappings)
        semantic_digest = _canonical_fingerprint(plan, _MIGRATION_ID_FIELDS)
        plan["migrationId"] = f"layout-{semantic_digest[:12]}"
        plan["planFingerprint"] = _canonical_fingerprint(plan, _MIGRATION_FINGERPRINT_FIELDS)
        return plan

    def _validate_migration_plan(self, plan: dict[str, Any], *, confirm_fingerprint: str | None = None, expected_migration_id: str | None = None, verify_current_manifest: bool = True) -> None:
        if not isinstance(plan, dict) or plan.get("planType") != "layout-migration" or plan.get("status") != "planned":
            raise ArtifactLibraryError("The layout migration plan is not applicable.")
        if not isinstance(confirm_fingerprint, str) or not confirm_fingerprint.strip():
            raise ArtifactLibraryError("Migration confirmation fingerprint is required.")
        if not isinstance(expected_migration_id, str) or not expected_migration_id.strip():
            raise ArtifactLibraryError("The expected migration ID is required.")
        if plan.get("migrationId") != expected_migration_id:
            raise ArtifactLibraryError("The migration ID does not match the approved plan.")
        approved_fingerprint = plan.get("planFingerprint")
        if not isinstance(approved_fingerprint, str) or not approved_fingerprint.strip():
            raise ArtifactLibraryError("The migration plan has no approved fingerprint.")
        canonical_fingerprint = _canonical_fingerprint(plan, _MIGRATION_FINGERPRINT_FIELDS)
        if canonical_fingerprint != approved_fingerprint or confirm_fingerprint != canonical_fingerprint:
            raise ArtifactLibraryError("Migration plan content or confirmation does not match the approved fingerprint.")
        if plan.get("projectFolder") != str(self.root):
            raise ArtifactLibraryError("Migration plan points outside the expected Project Folder.")
        if verify_current_manifest and _sha256(self.manifest_path) != plan.get("sourceProjectManifestSha256"):
            raise ArtifactLibraryError("Migration plan invalidated because the Project manifest changed.")
        mappings = plan.get("mappings")
        releases = plan.get("releases")
        if not isinstance(mappings, list) or not mappings or not isinstance(releases, list) or not releases:
            raise ArtifactLibraryError("The migration plan is incomplete.")
        if plan.get("currentTechnicalId") not in {release.get("technicalId") for release in releases if isinstance(release, dict)}:
            raise ArtifactLibraryError("The migration plan has no valid current release.")
        if plan.get("bytes") != sum(int(item.get("bytes", -1)) for item in mappings if isinstance(item, dict)):
            raise ArtifactLibraryError("The migration plan byte total is inconsistent.")
        destinations: set[str] = set()
        for mapping in mappings:
            if not isinstance(mapping, dict) or not isinstance(mapping.get("sourceRelative"), str) or not isinstance(mapping.get("destinationRelative"), str) or not isinstance(mapping.get("sha256"), str):
                raise ArtifactLibraryError("The migration plan contains an incomplete mapping.")
            destination_relative = mapping["destinationRelative"]
            if destination_relative in destinations:
                raise ArtifactLibraryError("The migration plan contains duplicate destinations.")
            destinations.add(destination_relative)
            source = _safe_relative(self.root, mapping["sourceRelative"], allow_missing=not verify_current_manifest)
            _safe_relative(self.root, destination_relative, allow_missing=True)
            if not source.exists():
                if verify_current_manifest:
                    raise ArtifactLibraryError(f"Migration source is missing: {mapping['sourceRelative']}.")
                continue
            if source.stat().st_size != int(mapping.get("bytes", -1)) or _sha256(source) != mapping.get("sha256"):
                raise ArtifactLibraryError(f"Migration plan invalidated for {mapping['sourceRelative']}.")

    def _verify_release_destination(self, base_root: Path, release: dict[str, Any], plan: dict[str, Any]) -> None:
        destination_folder = Path(release["destinationFolder"])
        destination = (base_root / destination_folder).resolve(strict=False)
        if not destination.is_dir():
            raise ArtifactLibraryError(f"Migration destination is missing: {release['destinationFolder']}")
        _assert_no_link_escape(base_root.resolve(), destination)
        release_mappings = [item for item in plan["mappings"] if str(item["destinationRelative"]).startswith(destination_folder.as_posix() + "/")]
        expected_files = {Path(item["destinationRelative"]).relative_to(destination_folder).as_posix() for item in release_mappings}
        actual_files = {_relative(destination, path) for path in destination.rglob("*") if path.is_file()}
        if actual_files != expected_files:
            raise ArtifactLibraryError(f"Migration destination contains unexpected or missing files: {release['destinationFolder']}")
        expected_dirs = {str(Path(item).parent).replace("\\", "/") for item in expected_files if Path(item).parent != Path(".")}
        actual_dirs = {_relative(destination, path) for path in destination.rglob("*") if path.is_dir()}
        if actual_dirs != expected_dirs:
            raise ArtifactLibraryError(f"Migration destination contains unexpected directories: {release['destinationFolder']}")
        for mapping in release_mappings:
            file_path = base_root / mapping["destinationRelative"]
            _assert_no_link_escape(base_root.resolve(), file_path)
            if mapping.get("role") == "release-manifest":
                manifest = read_json(file_path, None)
                if not isinstance(manifest, dict) or manifest.get("legacySource") != release.get("sourceFolder") or manifest.get("humanLabel") != release.get("humanLabel") or manifest.get("releaseState") != release.get("state"):
                    raise ArtifactLibraryError(f"Migration destination manifest does not match its plan: {release['destinationFolder']}")
            elif file_path.stat().st_size != int(mapping["bytes"]) or _sha256(file_path) != mapping["sha256"]:
                raise ArtifactLibraryError(f"Migration destination hash mismatch: {mapping['destinationRelative']}")

    def _verify_promoted_destinations(self, plan: dict[str, Any], *, require_all: bool = False) -> list[str]:
        existing: list[str] = []
        for release in plan["releases"]:
            destination = _safe_relative(self.root, release["destinationFolder"], allow_missing=True)
            if destination.exists():
                self._verify_release_destination(self.root, release, plan)
                existing.append(release["destinationFolder"])
        if require_all and len(existing) != len(plan["releases"]):
            raise ArtifactLibraryError("Not all planned migration destinations were promoted.")
        return existing

    def _set_destination_current_flags(self, plan: dict[str, Any]) -> None:
        for release in plan["releases"]:
            manifest_path = _safe_relative(self.root, f"{release['destinationFolder']}/manifest.json", allow_missing=False)
            manifest = read_json(manifest_path, None)
            if not isinstance(manifest, dict):
                raise ArtifactLibraryError(f"Promoted release manifest is missing: {manifest_path}")
            manifest["current"] = bool(release.get("current") is True)
            atomic_write_json(manifest_path, manifest)

    def _finish_promoted_migration(self, *, migration_id: str, plan: dict[str, Any], work: Path, original_project: dict[str, Any], verify_current_manifest: bool) -> dict[str, Any]:
        current = self._project()
        migration = current.get("artifactLibrary", {}).get("migration", {}) if isinstance(current.get("artifactLibrary"), dict) else {}
        if migration.get("migrationId") != migration_id:
            raise ArtifactLibraryError("The Project manifest has not switched to this migration.")
        self._validate_migration_plan(plan, confirm_fingerprint=plan.get("planFingerprint"), expected_migration_id=migration_id, verify_current_manifest=verify_current_manifest)
        self._verify_promoted_destinations(plan, require_all=True)
        removed = self._remove_legacy_sources(plan)
        self._set_destination_current_flags(plan)
        summary = {"migrationId": migration_id, "status": "promoted", "planFingerprint": plan["planFingerprint"], "bytes": plan["bytes"], "mappings": plan["mappings"], "releases": plan["releases"], "legacyManifests": plan.get("legacyManifests", {}), "originalProject": original_project, "removedLegacy": removed, "completedAt": _utc_now()}
        jobs = self.root / ".stem-comparison" / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        atomic_write_json(jobs / f"layout-{migration_id}.json", summary)
        shutil.rmtree(work, ignore_errors=True)
        return summary

    def apply_layout_migration(self, plan: dict[str, Any], *, confirm_fingerprint: str | None = None, expected_migration_id: str | None = None, should_cancel: Callable[[], bool] | None = None, interrupt_after_promotion: bool = False, interrupt_after_manifest_switch: bool = False, interrupt_after_destination_move: int | None = None, fail_after_promotions: int | None = None, fail_before_manifest_switch: bool = False) -> dict[str, Any]:
        self._validate_migration_plan(plan, confirm_fingerprint=confirm_fingerprint, expected_migration_id=expected_migration_id)
        migration_id = str(plan["migrationId"])
        work = self.root / ".stem-comparison" / "work" / "video" / migration_id
        stage = work / "stage"
        if work.exists():
            raise ArtifactLibraryError("An unfinished migration already exists; recover it before retrying.")
        original_project = read_json(self.manifest_path, None)
        if not isinstance(original_project, dict):
            raise ArtifactLibraryError("The Project manifest became unreadable before promotion.")
        work.mkdir(parents=True, exist_ok=False)
        atomic_write_json(work / "state.json", {"migrationId": migration_id, "phase": "staging", "plan": plan, "originalProject": original_project, "promotionTargets": [], "promotedDestinations": [], "promotionInFlight": None, "updatedAt": _utc_now()})
        try:
            for mapping in plan["mappings"]:
                if should_cancel and should_cancel():
                    raise ArtifactLibraryError("Migration cancelled before promotion; the original layout is unchanged.")
                source = _safe_relative(self.root, mapping["sourceRelative"])
                staged = stage / mapping["destinationRelative"]
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged)
            for mapping in plan["mappings"]:
                staged = stage / mapping["destinationRelative"]
                if not staged.is_file() or staged.stat().st_size != mapping["bytes"] or _sha256(staged) != mapping["sha256"]:
                    raise ArtifactLibraryError(f"Staged migration hash mismatch for {mapping['sourceRelative']}.")
            for release in plan["releases"]:
                source_manifest = self.root / release["sourceFolder"] / "manifest.json"
                raw_manifest = read_json(source_manifest, None)
                if not isinstance(raw_manifest, dict):
                    raise ArtifactLibraryError(f"Legacy release manifest is corrupt: {release['sourceFolder']}")
                destination_folder = Path(release["destinationFolder"])
                release_mappings = [item for item in plan["mappings"] if item["destinationRelative"].startswith(destination_folder.as_posix() + "/")]
                generated = self._release_metadata(raw_manifest, self.root / release["sourceFolder"], destination_folder=destination_folder.as_posix(), human_folder=release["humanLabel"], mappings=release_mappings, human_purpose=release.get("humanPurpose"), human_media_name=release.get("humanMediaFilename"))
                atomic_write_json(stage / destination_folder / "manifest.json", generated)
            if should_cancel and should_cancel():
                raise ArtifactLibraryError("Migration cancelled after validation; the original layout is unchanged.")
            atomic_write_json(work / "original-project.json", original_project)
            targets = [release["destinationFolder"] for release in plan["releases"]]
            atomic_write_json(work / "state.json", {"migrationId": migration_id, "phase": "promoting", "plan": plan, "originalProject": original_project, "promotionTargets": targets, "promotedDestinations": [], "promotionInFlight": None, "updatedAt": _utc_now()})
            promoted: list[str] = []
            for release in plan["releases"]:
                destination = _safe_relative(self.root, release["destinationFolder"], allow_missing=True)
                if destination.exists():
                    raise ArtifactLibraryError(f"Migration destination already exists: {release['destinationFolder']}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged_destination = stage / release["destinationFolder"]
                self._verify_release_destination(stage, release, plan)
                atomic_write_json(work / "state.json", {"migrationId": migration_id, "phase": "promoting", "plan": plan, "originalProject": original_project, "promotionTargets": targets, "promotedDestinations": promoted, "promotionInFlight": release["destinationFolder"], "updatedAt": _utc_now()})
                staged_destination.replace(destination)
                if interrupt_after_destination_move is not None and len(promoted) + 1 >= interrupt_after_destination_move:
                    raise ArtifactLibraryError("Injected post-move interruption; recovery is required.")
                promoted.append(release["destinationFolder"])
                atomic_write_json(work / "state.json", {"migrationId": migration_id, "phase": "promoting", "plan": plan, "originalProject": original_project, "promotionTargets": targets, "promotedDestinations": promoted, "promotionInFlight": None, "updatedAt": _utc_now()})
                if fail_after_promotions is not None and len(promoted) >= fail_after_promotions:
                    raise ArtifactLibraryError("Injected promotion failure; recovery is required.")
                if should_cancel and should_cancel():
                    raise ArtifactLibraryError("Migration cancelled during promotion; recovery is required.")
            self._verify_promoted_destinations(plan, require_all=True)
            if self._project() != original_project:
                raise ArtifactLibraryError("The Project manifest changed during promotion; recovery is required.")
            if fail_before_manifest_switch:
                raise ArtifactLibraryError("Injected pre-switch failure; recovery is required.")
            release_manifest_paths = {release["technicalId"]: f"{release['destinationFolder']}/manifest.json" for release in plan["releases"]}
            current_release = next(release for release in plan["releases"] if release["technicalId"] == plan["currentTechnicalId"])
            project = deepcopy(original_project)
            project["artifactLibrary"] = {"schemaVersion": LIBRARY_SCHEMA_VERSION, "layoutVersion": LAYOUT_VERSION, "currentRelease": {"releaseId": f"release:{plan['currentTechnicalId']}", "manifestPath": f"{current_release['destinationFolder']}/manifest.json", "humanLabel": current_release["humanLabel"], "state": "Approved"}, "releases": [{"releaseId": f"release:{release['technicalId']}", "manifestPath": release_manifest_paths[release["technicalId"]], "humanLabel": release["humanLabel"], "state": release["state"]} for release in plan["releases"]], "migration": {"migrationId": migration_id, "version": MIGRATION_VERSION, "sourceLayout": "video/packages", "promotedAt": _utc_now()}}
            atomic_write_json(self.manifest_path, project)
            if interrupt_after_manifest_switch:
                raise ArtifactLibraryError("Migration interrupted immediately after atomic manifest switch; recovery is required.")
            atomic_write_json(work / "state.json", {"migrationId": migration_id, "phase": "promoted", "plan": plan, "originalProject": original_project, "promotionTargets": targets, "promotedDestinations": promoted, "promotionInFlight": None, "updatedAt": _utc_now()})
            if interrupt_after_promotion:
                raise ArtifactLibraryError("Migration interrupted after atomic manifest promotion; recovery is required.")
            return self._finish_promoted_migration(migration_id=migration_id, plan=plan, work=work, original_project=original_project, verify_current_manifest=False)
        except Exception:
            if work.exists() and not (work / "state.json").is_file():
                shutil.rmtree(work, ignore_errors=True)
            raise

    def _remove_legacy_sources(self, plan: dict[str, Any]) -> list[str]:
        source_files = sorted({str(mapping["sourceRelative"]) for mapping in plan.get("mappings", [])}, key=lambda item: len(Path(item).parts), reverse=True)
        removed: list[str] = []
        for relative in source_files:
            path = _safe_relative(self.root, relative, allow_missing=True)
            if path.is_file():
                path.unlink()
                removed.append(relative)
        for source_folder in sorted({str(item["sourceFolder"]) for item in plan.get("releases", [])}, key=lambda item: len(Path(item).parts), reverse=True):
            folder = _safe_relative(self.root, source_folder, allow_missing=True)
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        return removed

    def recover_layout_migration(self, migration_id: str) -> dict[str, Any]:
        work = self.root / ".stem-comparison" / "work" / "video" / sanitize_human_component(migration_id, fallback="migration", limit=80)
        state_path = work / "state.json"
        state = read_json(state_path, None)
        if not isinstance(state, dict):
            raise ArtifactLibraryError("No recoverable layout migration was found.")
        phase = state.get("phase")
        if phase not in {"staging", "validated", "promoting", "promoted"} or not isinstance(state.get("plan"), dict) or not isinstance(state.get("originalProject"), dict):
            raise ArtifactLibraryError("The migration recovery state is corrupt.")
        if state.get("migrationId") != migration_id:
            raise ArtifactLibraryError("The migration recovery state belongs to a different migration.")
        plan = state["plan"]
        current_project = self._project()
        current_migration = current_project.get("artifactLibrary", {}).get("migration", {}) if isinstance(current_project.get("artifactLibrary"), dict) else {}
        switched = current_migration.get("migrationId") == migration_id
        self._validate_migration_plan(plan, confirm_fingerprint=plan.get("planFingerprint"), expected_migration_id=migration_id, verify_current_manifest=not switched)
        if switched:
            self._verify_promoted_destinations(plan, require_all=True)
            return self._finish_promoted_migration(migration_id=migration_id, plan=plan, work=work, original_project=state["originalProject"], verify_current_manifest=False)
        if current_project != state["originalProject"]:
            raise ArtifactLibraryError("The Project manifest changed before migration switch; refusing partial recovery.")
        planned_destinations = {release.get("destinationFolder") for release in plan["releases"]}
        recorded = state.get("promotedDestinations", [])
        in_flight = state.get("promotionInFlight")
        if not isinstance(recorded, list) or len(recorded) != len(set(recorded)) or any(destination not in planned_destinations for destination in recorded):
            raise ArtifactLibraryError("Migration recovery contains an ambiguous promoted destination.")
        if in_flight is not None and (not isinstance(in_flight, str) or in_flight not in planned_destinations or in_flight in recorded):
            raise ArtifactLibraryError("Migration recovery contains an ambiguous in-flight destination.")
        expected_created = set(recorded)
        if in_flight is not None:
            expected_created.add(in_flight)
        existing = self._verify_promoted_destinations(plan)
        if set(existing) != expected_created:
            raise ArtifactLibraryError("Migration destinations do not match the durable promotion journal; refusing recovery.")
        removed_destinations: list[str] = []
        for release in plan["releases"]:
            destination = _safe_relative(self.root, release["destinationFolder"], allow_missing=True)
            if release["destinationFolder"] in expected_created:
                self._verify_release_destination(self.root, release, plan)
                shutil.rmtree(destination)
                removed_destinations.append(release["destinationFolder"])
        if self._verify_promoted_destinations(plan):
            raise ArtifactLibraryError("Partial migration recovery left a planned destination behind.")
        shutil.rmtree(work, ignore_errors=True)
        return {"status": "cancelled", "migrationId": migration_id, "originalUnchanged": True, "removedDestinations": removed_destinations}

    def rollback_layout_migration(self, migration_id: str) -> dict[str, Any]:
        summary_path = self.root / ".stem-comparison" / "jobs" / f"layout-{sanitize_human_component(migration_id, fallback='migration', limit=80)}.json"
        summary = read_json(summary_path, None)
        if not isinstance(summary, dict) or summary.get("status") != "promoted":
            raise ArtifactLibraryError("Only a completed migration with an audit summary can be rolled back.")
        current = self._project().get("artifactLibrary", {}).get("migration", {})
        if current.get("migrationId") != migration_id:
            raise ArtifactLibraryError("The requested migration is not the current Project layout migration.")
        for mapping in sorted(summary.get("mappings", []), key=lambda item: len(Path(item["destinationRelative"]).parts), reverse=True):
            source = _safe_relative(self.root, mapping["sourceRelative"], allow_missing=True)
            destination = _safe_relative(self.root, mapping["destinationRelative"], allow_missing=False)
            if mapping.get("role") == "release-manifest":
                original_manifest = self.root / mapping["sourceRelative"]
                if not original_manifest.exists():
                    technical_id = next((release["technicalId"] for release in summary.get("releases", []) if f"{release['sourceFolder']}/manifest.json" == mapping["sourceRelative"]), None)
                    legacy = summary.get("legacyManifests", {}).get(str(technical_id)) if isinstance(summary.get("legacyManifests"), dict) else None
                    if isinstance(legacy, dict):
                        original_manifest.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_json(original_manifest, legacy)
                continue
            if not destination.is_file() or _sha256(destination) != mapping.get("sha256"):
                raise ArtifactLibraryError(f"Rollback source is missing or changed: {mapping['destinationRelative']}")
            source.parent.mkdir(parents=True, exist_ok=True)
            destination.replace(source)
        for release in summary.get("releases", []):
            destination_manifest = self.root / release["destinationFolder"] / "manifest.json"
            if destination_manifest.is_file():
                destination_manifest.unlink()
            destination_folder = self.root / release["destinationFolder"]
            if destination_folder.is_dir() and not any(destination_folder.iterdir()):
                destination_folder.rmdir()
        original_project = summary.get("originalProject")
        if not isinstance(original_project, dict):
            raise ArtifactLibraryError("The migration audit has no original Project manifest for rollback.")
        atomic_write_json(self.manifest_path, original_project)
        return {"status": "rolled-back", "migrationId": migration_id, "projectRestored": True}

    def promote_validated_render(self, *, job_id: str, snapshot: dict[str, Any], validation: dict[str, Any], staging_path: str | Path, ticket_id: str, output_filename: str) -> tuple[Path, Path]:
        """Move a validated render into a human release on projects already using layout v1."""
        if not isinstance(validation.get("checks"), dict) or not all(validation["checks"].values()):
            raise ArtifactLibraryError("Only a fully validated render can be promoted into the release library.")
        source = _safe_relative(self.root, _relative(self.root, normalized_path(staging_path)))
        if not source.is_file() or _sha256(source) != validation.get("sha256"):
            raise ArtifactLibraryError("The validated render source is missing or its hash changed.")
        config = self._video_config()
        project = self._project()
        artist = str(config.get("artist") or project.get("artist") or "")
        album = str(config.get("album") or project.get("albumName") or "")
        media_name = human_media_filename(artist=artist, album=album)
        base = human_release_folder_name(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"), state="Ready for review", album=album or "Album")
        destination_root = self.root / "video" / "releases"
        destination_root.mkdir(parents=True, exist_ok=True)
        folder_name = base
        index = 2
        while (destination_root / folder_name).exists():
            folder_name = human_release_folder_name(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"), state="Ready for review", album=album or "Album", collision_index=index)
            index += 1
        job_stage = source.parent.parent / "promotion"
        job_stage.mkdir(parents=True, exist_ok=False)
        moved_media = job_stage / media_name
        source.replace(moved_media)
        release_id = f"release:{job_id}"
        output_relative = (Path("video") / "releases" / folder_name / media_name).as_posix()
        manifest = {"schemaVersion": 1, "layoutVersion": LAYOUT_VERSION, "releaseId": release_id, "renderId": job_id, "technicalTicket": ticket_id, "kind": "real-video-release" if not str(snapshot.get("kind") or "").startswith("synthetic") else "synthetic-video-release", "humanLabel": folder_name, "releaseState": "Ready for review", "current": False, "outputPath": output_relative, "snapshot": snapshot, "validation": validation, "artifacts": {"albumVideo": {"path": media_name, "bytes": moved_media.stat().st_size, "sha256": validation["sha256"]}}, "provenance": {"sourceJobId": job_id, "sourceKind": snapshot.get("kind"), "historicalOutputsUsed": False, "stagingRelative": _relative(self.root, source)}, "createdAt": _utc_now()}
        atomic_write_json(job_stage / "manifest.json", manifest)
        destination = destination_root / folder_name
        job_stage.replace(destination)
        return destination / media_name, destination / "manifest.json"

    def cleanup_validated_staging(self, *, staging_folder: str | Path, promoted_path: str | Path, validation: dict[str, Any]) -> dict[str, Any]:
        """Remove only the exact validated staging folder after release promotion."""
        if not isinstance(validation.get("checks"), dict) or not all(validation["checks"].values()):
            raise ArtifactLibraryError("Unvalidated staging cannot be cleaned automatically.")
        staging = _safe_relative(self.root, _relative(self.root, normalized_path(staging_folder)))
        promoted = _safe_relative(self.root, _relative(self.root, normalized_path(promoted_path)))
        if not staging.is_dir() or staging.name.casefold() != "staging":
            raise ArtifactLibraryError("Only an exact render staging folder can be cleaned.")
        if not promoted.is_file() or _sha256(promoted) != validation.get("sha256"):
            raise ArtifactLibraryError("The promoted release hash is not verified; staging is retained.")
        removed_bytes = sum(path.stat().st_size for path in staging.rglob("*") if path.is_file())
        shutil.rmtree(staging)
        return {"status": "cleaned", "stagingPath": _relative(self.root, staging), "bytes": removed_bytes, "promotedPath": _relative(self.root, promoted)}

    def plan_release_promotion(self, *, staging_folder: str | Path, release_manifest: dict[str, Any]) -> dict[str, Any]:
        """Create a future render promotion plan; callers still need explicit apply authority."""
        stage = _safe_relative(self.root, staging_folder)
        if not stage.is_dir():
            raise ArtifactLibraryError("Release staging folder is missing.")
        if not release_manifest.get("releaseId") or _release_state(release_manifest) not in RELEASE_STATES:
            raise ArtifactLibraryError("Release promotion requires a stable ID and human release state.")
        return {"schemaVersion": LIBRARY_SCHEMA_VERSION, "planType": "release-promotion", "stagingFolder": _relative(self.root, stage), "releaseManifest": release_manifest, "stagingHash": _digest([(path.relative_to(stage).as_posix(), path.stat().st_size, _sha256(path)) for path in sorted(stage.rglob("*")) if path.is_file()]), "createdAt": _utc_now()}
