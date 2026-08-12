from __future__ import annotations

import asyncio
import json
import os
from multiprocessing import get_context
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import AppPaths, ensure_app_paths
from .catalogue import apply_fast_default_slots, discover_catalogue, save_candidate_slots
from .exporting import build_export_plan, export_album
from .listening import approve_and_select, approve_and_select_all, confirm_output_semantics, invalidate_output, select_candidate, update_loop
from .preflight import collect_preflight
from .project_migration import migration_preview, migrate_project
from .project_artifact_library import ArtifactLibraryError, ProjectArtifactLibrary, cleanup_plan_file_sha256
from .processing import ProcessingError, cleanup_temporary, create_album_job, create_calibration_job, disk_space_check, latest_calibration_state, reconcile_jobs, run_album_job, run_calibration_job, skip_calibration, stop_calibration_state
from .projects import ProjectError, choose_folder, create_project, discover_project_manifests, open_project_manifest, relink_source, remember_project, remove_recent_project, rescan_project, resolve_project_creation
from .settings import load_settings, save_settings, update_setting
from .video import build_video_state, configure_video, video_asset_path, video_audio_path
from .video_preparation import VideoPreparationError, refresh_video_preparation
from .video_package import VideoPackageError, generate_real_video_package, generate_synthetic_video_package, read_current_video_package
from .audio_package import AudioPackageError, read_audio_job, read_current_audio_package, retry_audio_job, start_audio_export, stop_audio_job
from .video_fast_export import start_fast_render
from .video_proof import VideoProofError, approve_current_proof, read_current_proof_pack, read_proof_job, reject_current_proof, retry_proof_job, start_proof_pack, stop_proof_job
from .video_tail_audition import VideoTailAuditionError, build_tail_audition_state, save_tail_audition_decision
from .video_render import VideoRenderError, read_video_render_job, retry_video_render as retry_video_render_job, start_real_render, start_synthetic_render, stop_video_render_job


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT / "frontend" / "dist"


def create_app(*, frontend_dist: Path | None = None, paths: AppPaths | None = None) -> FastAPI:
    app = FastAPI(title="AlbumPress Studio", version="0.1.0", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
    )
    paths = paths or ensure_app_paths()
    dist = frontend_dist or FRONTEND_DIST
    app.state.calibration_processes = {}
    app.state.calibration_controls = {}
    app.state.video_render_processes = {}
    app.state.video_proof_processes = {}
    app.state.audio_package_processes = {}

    def current_manifest_path() -> Path:
        settings = load_settings(paths)
        manifest_path = settings.get("lastProjectManifest")
        if not manifest_path:
            raise HTTPException(status_code=404, detail="No Album Project is open")
        path = Path(str(manifest_path))
        if not path.exists():
            raise HTTPException(status_code=404, detail="The current Album Project manifest is missing")
        return path

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "albumpress-studio"}

    @app.get("/api/preflight")
    async def preflight() -> dict[str, object]:
        return collect_preflight()

    @app.get("/api/catalogue")
    async def catalogue() -> dict[str, object]:
        return discover_catalogue(paths=paths)

    @app.post("/api/catalogue/refresh")
    async def refresh_catalogue() -> dict[str, object]:
        return discover_catalogue(paths=paths)

    @app.get("/api/settings")
    async def settings() -> dict[str, object]:
        value = load_settings(paths)
        return {
            **value,
            "modelCachePath": str(paths.model_cache),
            "logPath": str(paths.log_file),
            "projectLibrary": str(value.get("projectLibrary")),
        }

    @app.get("/api/storage")
    async def storage() -> dict[str, object]:
        value = load_settings(paths)
        return {
            "projectLibrary": str(value.get("projectLibrary")),
            "modelCachePath": str(paths.model_cache),
            "logPath": str(paths.log_file),
        }

    @app.post("/api/storage/open")
    async def open_storage(request: Request) -> dict[str, str]:
        payload = await request.json()
        kind = str(payload.get("kind")) if isinstance(payload, dict) else ""
        value = load_settings(paths)
        locations = {"projectLibrary": Path(str(value.get("projectLibrary"))), "modelCache": paths.model_cache, "logs": paths.log_file}
        target = locations.get(kind)
        if target is None:
            raise HTTPException(status_code=422, detail="Unknown storage location")
        target = target if target.is_dir() else target.parent
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(target))
        return {"path": str(target)}

    @app.get("/api/projects")
    async def projects() -> dict[str, object]:
        return discover_project_manifests(load_settings(paths))

    @app.post("/api/projects/preview")
    async def preview_project(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("sourcePath"):
            raise HTTPException(status_code=422, detail="sourcePath is required")
        try:
            return resolve_project_creation(
                str(payload["sourcePath"]),
                project_name=str(payload.get("projectName") or ""),
                project_library=str(payload.get("projectLibrary")) if payload.get("projectLibrary") else load_settings(paths).get("projectLibrary"),
                project_folder=str(payload.get("projectFolder")) if payload.get("projectFolder") else None,
            )
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/projects/current")
    async def current_project() -> dict[str, object]:
        settings = load_settings(paths)
        manifest_path = settings.get("lastProjectManifest")
        if not manifest_path or not Path(str(manifest_path)).exists():
            raise HTTPException(status_code=404, detail="No Album Project is open")
        try:
            return open_project_manifest(Path(str(manifest_path)))
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/video/config")
    async def video_config() -> dict[str, object]:
        try:
            return build_video_state(current_manifest_path())
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/configure")
    async def video_configure(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Expected a JSON object")
        try:
            return configure_video(current_manifest_path(), payload)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/preparation/refresh")
    async def refresh_video_preparation_endpoint(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Expected a JSON object")
        try:
            return refresh_video_preparation(current_manifest_path(), payload)
        except VideoPreparationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/video/proof")
    async def video_proof() -> dict[str, object]:
        try:
            return read_current_proof_pack(current_manifest_path())
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/video/tail-audition")
    async def video_tail_audition() -> dict[str, object]:
        try:
            return build_tail_audition_state(current_manifest_path())
        except VideoTailAuditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/tail-audition/{track_id}/decision")
    async def save_video_tail_audition_decision(track_id: str, request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="A tail audition decision is required") from None
        try:
            return save_tail_audition_decision(current_manifest_path(), track_id, str(payload.get("decision", "")))
        except VideoTailAuditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/proof/generate")
    async def generate_video_proof() -> dict[str, object]:
        try:
            return start_proof_pack(current_manifest_path(), app.state.video_proof_processes, synthetic=False)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/proof/synthetic")
    async def generate_synthetic_video_proof() -> dict[str, object]:
        try:
            return start_proof_pack(current_manifest_path(), app.state.video_proof_processes, synthetic=True)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/video/proof/jobs/{job_id}")
    async def video_proof_job(job_id: str) -> dict[str, object]:
        try:
            return read_proof_job(current_manifest_path(), job_id, app.state.video_proof_processes)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/video/proof/jobs/{job_id}/stop")
    async def stop_video_proof_job(job_id: str) -> dict[str, object]:
        try:
            return stop_proof_job(current_manifest_path(), job_id, app.state.video_proof_processes)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/proof/jobs/{job_id}/retry")
    async def retry_video_proof_job(job_id: str) -> dict[str, object]:
        try:
            return retry_proof_job(current_manifest_path(), job_id, app.state.video_proof_processes)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/proof/{proof_id}/approve")
    async def approve_video_proof(proof_id: str) -> dict[str, object]:
        try:
            return approve_current_proof(current_manifest_path(), proof_id)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/proof/{proof_id}/reject")
    async def reject_video_proof(proof_id: str, request: Request) -> dict[str, object]:
        payload = await request.json()
        reason = payload.get("reason") if isinstance(payload, dict) else None
        try:
            return reject_current_proof(current_manifest_path(), proof_id, str(reason) if reason else None)
        except (ProjectError, VideoProofError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/video/proof/{proof_id}/asset/{asset_path:path}")
    async def video_proof_asset(proof_id: str, asset_path: str) -> FileResponse:
        if len(proof_id) != 32 or any(character not in "0123456789abcdef" for character in proof_id.casefold()):
            raise HTTPException(status_code=404, detail="Proof Pack asset not found")
        root = current_manifest_path().parent
        proof_root = (root / "video" / "proofs" / proof_id).resolve(strict=False)
        candidate = (proof_root / asset_path).resolve(strict=False)
        try:
            candidate.relative_to(proof_root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Proof Pack asset not found") from exc
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Proof Pack asset not found")
        return FileResponse(candidate)

    @app.post("/api/video/render/synthetic")
    async def start_video_synthetic_render() -> dict[str, object]:
        try:
            return start_synthetic_render(current_manifest_path(), app.state.video_render_processes)
        except VideoRenderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/render/real")
    async def start_real_video_render() -> dict[str, object]:
        try:
            return start_real_render(current_manifest_path(), app.state.video_render_processes)
        except VideoRenderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/render/fast/synthetic")
    async def start_fast_synthetic_video_render() -> dict[str, object]:
        try:
            return start_fast_render(current_manifest_path(), app.state.video_render_processes, synthetic=True)
        except VideoRenderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/render/fast/real")
    async def start_fast_real_video_render() -> dict[str, object]:
        try:
            return start_fast_render(current_manifest_path(), app.state.video_render_processes, synthetic=False)
        except VideoRenderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/render/{job_id}/retry")
    async def retry_video_render(job_id: str) -> dict[str, object]:
        try:
            return retry_video_render_job(current_manifest_path(), job_id, app.state.video_render_processes)
        except VideoRenderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/video/render/{job_id}")
    async def video_render_status(job_id: str) -> dict[str, object]:
        try:
            return read_video_render_job(current_manifest_path(), job_id, app.state.video_render_processes)
        except VideoRenderError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/video/render/{job_id}/stop")
    async def stop_video_render(job_id: str) -> dict[str, object]:
        try:
            return stop_video_render_job(current_manifest_path(), job_id, app.state.video_render_processes)
        except VideoRenderError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/video/package")
    async def video_package() -> dict[str, object]:
        try:
            return read_current_video_package(current_manifest_path())
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/package/synthetic")
    async def create_synthetic_video_package(request: Request) -> dict[str, object]:
        payload = await request.json()
        notes = payload.get("notes") if isinstance(payload, dict) else None
        try:
            return generate_synthetic_video_package(current_manifest_path(), str(notes) if notes is not None else None)
        except VideoPackageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/video/package/real")
    async def create_real_video_package(request: Request) -> dict[str, object]:
        payload = await request.json()
        notes = payload.get("notes") if isinstance(payload, dict) else None
        try:
            return generate_real_video_package(current_manifest_path(), str(notes) if notes is not None else None)
        except VideoPackageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/audio/package")
    async def audio_package() -> dict[str, object]:
        try:
            return read_current_audio_package(current_manifest_path().parent)
        except (ProjectError, AudioPackageError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/audio/package/export")
    async def export_audio_package(request: Request) -> dict[str, object]:
        payload = await request.json()
        options = payload.get("options") if isinstance(payload, dict) and isinstance(payload.get("options"), dict) else {}
        force = bool(payload.get("force")) if isinstance(payload, dict) else False
        try:
            return start_audio_export(current_manifest_path(), app.state.audio_package_processes, options, force=force)
        except (ProjectError, AudioPackageError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/audio/package/jobs/{job_id}")
    async def audio_package_job(job_id: str) -> dict[str, object]:
        try:
            return read_audio_job(current_manifest_path(), job_id, app.state.audio_package_processes)
        except (ProjectError, AudioPackageError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/audio/package/jobs/{job_id}/stop")
    async def stop_audio_package_job(job_id: str) -> dict[str, object]:
        try:
            return stop_audio_job(current_manifest_path(), job_id, app.state.audio_package_processes)
        except (ProjectError, AudioPackageError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/audio/package/jobs/{job_id}/retry")
    async def retry_audio_package_job(job_id: str) -> dict[str, object]:
        try:
            return retry_audio_job(current_manifest_path(), job_id, app.state.audio_package_processes)
        except (ProjectError, AudioPackageError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/audio/package/open-folder")
    async def open_audio_package_folder() -> dict[str, str]:
        package = read_current_audio_package(current_manifest_path().parent)
        if not package.get("ready"):
            raise HTTPException(status_code=409, detail="No ready Audio Mix Package is available")
        folder = Path(str(package["packageFolder"]))
        if os.name == "nt":
            os.startfile(str(folder))
        return {"path": str(folder)}

    @app.post("/api/video/package/open-folder")
    async def open_video_package_folder() -> dict[str, str]:
        package = read_current_video_package(current_manifest_path())
        if not package.get("ready"):
            raise HTTPException(status_code=409, detail="No ready Video Package is available")
        folder = Path(str(package["packageFolder"]))
        if os.name == "nt":
            os.startfile(str(folder))
        return {"path": str(folder)}

    @app.post("/api/projects/pick-folder")
    async def pick_folder() -> dict[str, str | None]:
        return {"path": choose_folder()}

    @app.post("/api/projects/open")
    async def open_album_project(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="A project open payload is required")
        try:
            if payload.get("manifestPath"):
                manifest = open_project_manifest(str(payload["manifestPath"]))
            elif payload.get("sourcePath"):
                settings = load_settings(paths)
                manifest = create_project(
                    payload["sourcePath"],
                    project_name=str(payload.get("projectName") or ""),
                    project_library=str(payload.get("projectLibrary")) if payload.get("projectLibrary") else settings.get("projectLibrary"),
                    project_folder=str(payload.get("projectFolder")) if payload.get("projectFolder") else None,
                )
            else:
                raise HTTPException(status_code=422, detail="sourcePath or manifestPath is required")
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        manifest_path = Path(str(manifest.get("projectFolder") or manifest.get("outputFolder"))) / "project.json"
        settings = remember_project(load_settings(paths), manifest_path)
        save_settings(settings, paths)
        return manifest

    @app.post("/api/projects/open-folder")
    async def open_project_folder(request: Request) -> dict[str, str]:
        payload = await request.json()
        manifest_path = str(payload.get("manifestPath")) if isinstance(payload, dict) and payload.get("manifestPath") else str(current_manifest_path())
        try:
            manifest = open_project_manifest(manifest_path)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        folder = Path(str(manifest["projectFolder"]))
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail="Project Folder not found")
        if os.name == "nt":
            os.startfile(str(folder))
        return {"path": str(folder)}

    @app.post("/api/projects/remove-recent")
    async def remove_recent(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("manifestPath"):
            raise HTTPException(status_code=422, detail="manifestPath is required")
        settings = remove_recent_project(load_settings(paths), str(payload["manifestPath"]))
        save_settings(settings, paths)
        return discover_project_manifests(settings)

    @app.post("/api/projects/relink-source")
    async def relink_project_source(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("sourcePath"):
            raise HTTPException(status_code=422, detail="sourcePath is required")
        try:
            manifest = relink_source(str(current_manifest_path()), str(payload["sourcePath"]))
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return manifest

    @app.post("/api/projects/migration-preview")
    async def preview_project_migration(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("destinationPath"):
            raise HTTPException(status_code=422, detail="destinationPath is required")
        manifest_path = str(payload.get("manifestPath")) if payload.get("manifestPath") else str(current_manifest_path())
        try:
            return migration_preview(manifest_path, str(payload["destinationPath"]))
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/migrate")
    async def migrate_album_project(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("destinationPath"):
            raise HTTPException(status_code=422, detail="destinationPath is required")
        manifest_path = str(payload.get("manifestPath")) if payload.get("manifestPath") else str(current_manifest_path())
        try:
            result = migrate_project(manifest_path, str(payload["destinationPath"]))
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        destination_manifest = Path(str(result["destinationProjectFolder"])) / "project.json"
        save_settings(remember_project(load_settings(paths), destination_manifest), paths)
        result["current"] = True
        return result

    @app.get("/api/projects/storage/artifacts")
    async def project_artifact_storage() -> dict[str, object]:
        try:
            return ProjectArtifactLibrary(current_manifest_path().parent).inventory()
        except (ProjectError, ArtifactLibraryError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/storage/cleanup/preview")
    async def project_artifact_cleanup_preview(request: Request) -> dict[str, object]:
        payload = await request.json()
        verify_hashes = bool(payload.get("verifyHashes")) if isinstance(payload, dict) else False
        try:
            plan = ProjectArtifactLibrary(current_manifest_path().parent).plan_cleanup(verify_hashes=verify_hashes)
            return {**plan, "planFileSha256": cleanup_plan_file_sha256(plan)}
        except (ProjectError, ArtifactLibraryError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/storage/cleanup/apply")
    async def project_artifact_cleanup_apply(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
            raise HTTPException(status_code=422, detail="An unchanged cleanup plan is required")
        try:
            return ProjectArtifactLibrary(current_manifest_path().parent).apply_cleanup(
                payload["plan"],
                confirm_fingerprint=str(payload.get("confirmFingerprint") or ""),
                confirm_plan_file_sha256=str(payload.get("confirmPlanFileSha256") or ""),
            )
        except (ProjectError, ArtifactLibraryError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/storage/migration/preview")
    async def project_artifact_migration_preview() -> dict[str, object]:
        try:
            return ProjectArtifactLibrary(current_manifest_path().parent).plan_layout_migration()
        except (ProjectError, ArtifactLibraryError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/rescan")
    async def rescan_album_project() -> dict[str, object]:
        settings = load_settings(paths)
        manifest_path = settings.get("lastProjectManifest")
        if not manifest_path:
            raise HTTPException(status_code=404, detail="No Album Project is open")
        try:
            return rescan_project(str(manifest_path))
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/candidates")
    async def save_candidates(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("slots"), dict):
            raise HTTPException(status_code=422, detail="slots is required")
        settings = load_settings(paths)
        manifest_path = settings.get("lastProjectManifest")
        if not manifest_path:
            raise HTTPException(status_code=404, detail="No Album Project is open")
        live_catalogue = discover_catalogue(paths=paths)
        if not live_catalogue.get("live"):
            raise HTTPException(status_code=409, detail="Live Candidate discovery is unavailable; choices were not changed")
        try:
            return save_candidate_slots(str(manifest_path), payload["slots"], live_catalogue)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/candidates/fast-default")
    async def set_fast_default_candidates() -> dict[str, object]:
        settings = load_settings(paths)
        manifest_path = settings.get("lastProjectManifest")
        if not manifest_path:
            raise HTTPException(status_code=404, detail="No Album Project is open")
        live_catalogue = discover_catalogue(paths=paths)
        if not live_catalogue.get("live"):
            raise HTTPException(status_code=409, detail="Live Candidate discovery is unavailable; defaults were not changed")
        try:
            return apply_fast_default_slots(str(manifest_path), live_catalogue)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/api/projects/loops/{track_id}")
    async def patch_loop(track_id: str, request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="Loop state must be a JSON object")
        try:
            return update_loop(str(current_manifest_path()), track_id, payload)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/selections")
    async def post_selection(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("trackId") or not payload.get("slot"):
            raise HTTPException(status_code=422, detail="trackId and slot are required")
        try:
            return select_candidate(str(current_manifest_path()), str(payload["trackId"]), str(payload["slot"]), str(payload["outputId"]) if payload.get("outputId") else None)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/outputs/approve-select")
    async def post_approve_select(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("trackId") or not payload.get("slot"):
            raise HTTPException(status_code=422, detail="trackId and slot are required")
        try:
            return approve_and_select(str(current_manifest_path()), str(payload["trackId"]), str(payload["slot"]), str(payload["outputId"]) if payload.get("outputId") else None)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/outputs/approve-select-all")
    async def post_approve_select_all() -> dict[str, object]:
        try:
            return approve_and_select_all(str(current_manifest_path()))
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/outputs/invalidate")
    async def post_invalidate_output(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("outputId"):
            raise HTTPException(status_code=422, detail="outputId is required")
        reason = str(payload.get("reason") or "Output rejected during human review.")
        try:
            return invalidate_output(str(current_manifest_path()), str(payload["outputId"]), reason)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/outputs/semantic-confirmation")
    async def post_semantic_confirmation(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("outputId"):
            raise HTTPException(status_code=422, detail="outputId is required")
        try:
            return confirm_output_semantics(str(current_manifest_path()), str(payload["outputId"]))
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/projects/export/status")
    async def export_status() -> dict[str, object]:
        try:
            return build_export_plan(current_manifest_path())
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/projects/export")
    async def export_project(request: Request) -> dict[str, object]:
        payload = await request.json()
        destination = str(payload["destinationPath"]) if isinstance(payload, dict) and payload.get("destinationPath") else None
        try:
            return export_album(current_manifest_path(), destination)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/export/open-folder")
    async def open_export_folder() -> dict[str, str]:
        plan = build_export_plan(current_manifest_path())
        destination = Path(plan["destinationFolder"])
        destination.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(destination))
        return {"path": str(destination)}

    @app.get("/api/process/calibration/status")
    async def calibration_status() -> dict[str, object]:
        manifest_path = current_manifest_path()
        reconcile_jobs(manifest_path, set(app.state.calibration_processes))
        state = latest_calibration_state(manifest_path)
        if state is None:
            raise HTTPException(status_code=404, detail="No calibration has been started")
        return state

    @app.get("/api/process/status")
    async def process_status() -> dict[str, object]:
        manifest_path = current_manifest_path()
        reconcile_jobs(manifest_path, set(app.state.calibration_processes))
        state = latest_calibration_state(manifest_path)
        if state is None:
            raise HTTPException(status_code=404, detail="No processing job has been started")
        return state

    @app.post("/api/process/calibration")
    async def start_calibration(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
        manifest_path = current_manifest_path()
        reconcile_jobs(manifest_path, set(app.state.calibration_processes))
        space = disk_space_check(manifest_path)
        if not space["ready"]:
            raise HTTPException(status_code=409, detail=space["detail"])
        current = latest_calibration_state(manifest_path)
        if current and current.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="A calibration is already running")
        try:
            job_id, state_path, state = create_calibration_job(
                manifest_path,
                payload.get("trackId"),
                preview_start_seconds=payload.get("previewStartSeconds"),
                preview_duration_seconds=payload.get("previewDurationSeconds"),
            )
        except ProcessingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        context = get_context("spawn")
        stop_event = context.Event()
        process = context.Process(
            target=run_calibration_job,
            args=(str(manifest_path), str(state_path), str(paths.model_cache)),
            kwargs={"stop_event": stop_event},
            name=f"albumpress-studio-{job_id}",
        )
        process.daemon = True
        process.start()
        app.state.calibration_processes[job_id] = process
        app.state.calibration_controls[job_id] = stop_event
        return state

    @app.post("/api/process/candidate")
    async def start_candidate_for_track(request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload.get("trackId") or not payload.get("slot"):
            raise HTTPException(status_code=422, detail="trackId and slot are required")
        manifest_path = current_manifest_path()
        reconcile_jobs(manifest_path, set(app.state.calibration_processes))
        space = disk_space_check(manifest_path)
        if not space["ready"]:
            raise HTTPException(status_code=409, detail=space["detail"])
        current = latest_calibration_state(manifest_path)
        if current and current.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="A processing job is already running")
        try:
            job_id, state_path, state = create_album_job(manifest_path, track_ids={str(payload["trackId"])}, slots={str(payload["slot"])}, kind="candidate")
        except ProcessingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        context = get_context("spawn")
        stop_event = context.Event()
        process = context.Process(target=run_album_job, args=(str(manifest_path), str(state_path), str(paths.model_cache)), kwargs={"stop_event": stop_event}, name=f"albumpress-studio-{job_id}")
        process.daemon = True
        process.start()
        app.state.calibration_processes[job_id] = process
        app.state.calibration_controls[job_id] = stop_event
        return state

    @app.post("/api/process/calibration/skip")
    async def skip_calibration_route() -> dict[str, object]:
        manifest_path = current_manifest_path()
        return skip_calibration(manifest_path)

    @app.post("/api/process/album")
    async def start_album_processing() -> dict[str, object]:
        manifest_path = current_manifest_path()
        reconcile_jobs(manifest_path, set(app.state.calibration_processes))
        space = disk_space_check(manifest_path)
        if not space["ready"]:
            raise HTTPException(status_code=409, detail=space["detail"])
        current = latest_calibration_state(manifest_path)
        if current and current.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="A processing job is already running")
        try:
            job_id, state_path, state = create_album_job(manifest_path)
        except ProcessingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        context = get_context("spawn")
        stop_event = context.Event()
        process = context.Process(
            target=run_album_job,
            args=(str(manifest_path), str(state_path), str(paths.model_cache)),
            kwargs={"stop_event": stop_event},
            name=f"albumpress-studio-{job_id}",
        )
        process.daemon = True
        process.start()
        app.state.calibration_processes[job_id] = process
        app.state.calibration_controls[job_id] = stop_event
        return state

    async def launch_scoped_album(request: Request, *, force: bool) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
        manifest_path = current_manifest_path()
        reconcile_jobs(manifest_path, set(app.state.calibration_processes))
        space = disk_space_check(manifest_path)
        if not space["ready"]:
            raise HTTPException(status_code=409, detail=space["detail"])
        current = latest_calibration_state(manifest_path)
        if current and current.get("status") in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="A processing job is already running")
        scope = str(payload.get("scope", "output"))
        track_id = str(payload["trackId"]) if payload.get("trackId") else None
        slot = str(payload["slot"]) if payload.get("slot") else None
        track_ids = {track_id} if scope in {"output", "track"} and track_id else None
        slots = {slot} if scope in {"output", "candidate"} and slot else None
        if scope in {"output", "track"} and not track_id or scope in {"output", "candidate"} and not slot:
            raise HTTPException(status_code=422, detail="A trackId and/or slot is required for this reprocess scope")
        try:
            job_id, state_path, state = create_album_job(manifest_path, track_ids=track_ids, slots=slots, force=force)
        except ProcessingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        context = get_context("spawn")
        stop_event = context.Event()
        process = context.Process(target=run_album_job, args=(str(manifest_path), str(state_path), str(paths.model_cache)), kwargs={"stop_event": stop_event}, name=f"albumpress-studio-{job_id}")
        process.daemon = True
        process.start()
        app.state.calibration_processes[job_id] = process
        app.state.calibration_controls[job_id] = stop_event
        return state

    @app.post("/api/process/retry")
    async def retry_processing(request: Request) -> dict[str, object]:
        return await launch_scoped_album(request, force=False)

    @app.post("/api/process/reprocess")
    async def force_reprocess(request: Request) -> dict[str, object]:
        return await launch_scoped_album(request, force=True)

    @app.post("/api/process/cleanup")
    async def cleanup_process_temporary() -> dict[str, object]:
        return cleanup_temporary(current_manifest_path())

    @app.get("/api/process/calibration/{job_id}")
    async def calibration_job(job_id: str) -> dict[str, object]:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Calibration job not found")
        return json.loads(state_path.read_text(encoding="utf-8"))

    @app.get("/api/process/album/{job_id}")
    async def album_job(job_id: str) -> dict[str, object]:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Album processing job not found")
        return json.loads(state_path.read_text(encoding="utf-8"))

    @app.get("/api/process/calibration/{job_id}/outputs/{slot}")
    async def calibration_output(job_id: str, slot: str) -> FileResponse:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists() or slot not in {"A", "B", "C", "D"}:
            raise HTTPException(status_code=404, detail="Calibration Output not found")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        task = next((item for item in state.get("tasks", []) if item.get("slot") == slot), None)
        output_id = task.get("outputId") if task else None
        from .projects import load_manifest

        manifest = load_manifest(manifest_path)
        output = manifest.get("outputs", {}).get(output_id) if output_id else None
        output_path = Path(str(output.get("path"))) if isinstance(output, dict) and output.get("status") == "valid" and output.get("path") else None
        if output_path is None:
            raise HTTPException(status_code=404, detail="Calibration Output not found")
        output_path = output_path.resolve(strict=False)
        try:
            output_path.relative_to(Path(manifest["outputFolder"]).resolve(strict=False))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Calibration Output is outside the project workspace") from exc
        if not output_path.is_file():
            raise HTTPException(status_code=404, detail="Calibration Output is no longer available")
        return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)

    @app.get("/api/process/album/{job_id}/outputs/{track_id}/{slot}")
    async def album_output(job_id: str, track_id: str, slot: str) -> FileResponse:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists() or slot not in {"A", "B", "C", "D"}:
            raise HTTPException(status_code=404, detail="Album Output not found")
        from .projects import load_manifest

        manifest = load_manifest(manifest_path)
        output_id = f"album:{track_id}:{slot}"
        output = manifest.get("outputs", {}).get(output_id)
        output_path = Path(str(output.get("path"))) if isinstance(output, dict) and output.get("status") == "valid" and output.get("path") else None
        if output_path is None:
            raise HTTPException(status_code=404, detail="Album Output not found")
        output_path = output_path.resolve(strict=False)
        try:
            output_path.relative_to(Path(manifest["outputFolder"]).resolve(strict=False))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Album Output is outside the project workspace") from exc
        if not output_path.is_file():
            raise HTTPException(status_code=404, detail="Album Output is no longer available")
        return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)

    @app.get("/api/video/assets/{kind}")
    async def video_asset(kind: str) -> FileResponse:
        try:
            asset_path = video_asset_path(current_manifest_path(), kind)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        media_type = "image/png" if kind in {"artwork", "texture", "brand-monogram", "brand-lockup", "brand-watermark"} else ("image/svg+xml" if kind == "brand-vector" else ("application/json" if kind == "brand-approvalManifest" else ("font/ttf" if kind in {"display-font", "display-font-italic"} else "font/woff2")))
        return FileResponse(asset_path, media_type=media_type, filename=asset_path.name, headers={"Cache-Control": "no-store"})

    @app.get("/api/video/audio/{track_id}", response_model=None)
    async def video_audio(track_id: str, request: Request) -> Response | StreamingResponse:
        try:
            media_path = video_audio_path(current_manifest_path(), track_id)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        size = media_path.stat().st_size
        start, end = 0, size - 1
        status_code = 200
        range_header = request.headers.get("range")
        if range_header:
            try:
                unit, value = range_header.split("=", 1)
                if unit != "bytes" or "," in value:
                    raise ValueError
                start_text, end_text = value.split("-", 1)
                if start_text:
                    start = int(start_text)
                    end = int(end_text) if end_text else size - 1
                else:
                    suffix = int(end_text)
                    start = max(0, size - suffix)
                    end = size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                status_code = 206
            except ValueError:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        length = end - start + 1

        def stream():
            with media_path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Content-Disposition": "inline"}
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(stream(), status_code=status_code, media_type="audio/wav", headers=headers)

    @app.get("/api/projects/media/{track_id}/{slot}", response_model=None)
    async def project_media(track_id: str, slot: str, request: Request) -> Response | StreamingResponse:
        manifest_path = current_manifest_path()
        if slot not in {"A", "B", "C", "D"}:
            raise HTTPException(status_code=404, detail="Project media not found")
        from .projects import load_manifest

        manifest = load_manifest(manifest_path)
        records = [
            item for item in manifest.get("outputs", {}).values()
            if isinstance(item, dict) and item.get("trackId") == track_id and item.get("slot") == slot and item.get("status") == "valid" and item.get("path")
        ]
        records.sort(key=lambda item: (0 if str(item.get("outputId", "")).startswith("album:") else 1, str(item.get("validatedAt", ""))))
        if not records:
            raise HTTPException(status_code=404, detail="Project media not found")
        media_path = Path(str(records[0]["path"])).resolve(strict=False)
        try:
            media_path.relative_to(Path(manifest["outputFolder"]).resolve(strict=False))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Project media is outside the project workspace") from exc
        if not media_path.is_file():
            raise HTTPException(status_code=404, detail="Project media is no longer available")
        size = media_path.stat().st_size
        start, end = 0, size - 1
        status_code = 200
        range_header = request.headers.get("range")
        if range_header:
            try:
                unit, value = range_header.split("=", 1)
                if unit != "bytes" or "," in value:
                    raise ValueError
                start_text, end_text = value.split("-", 1)
                if start_text:
                    start = int(start_text)
                    end = int(end_text) if end_text else size - 1
                else:
                    suffix = int(end_text)
                    start = max(0, size - suffix)
                    end = size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                status_code = 206
            except ValueError:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

        length = end - start + 1

        def stream():
            with media_path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Content-Disposition": "inline"}
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(stream(), status_code=status_code, media_type="audio/wav", headers=headers)

    @app.post("/api/process/calibration/{job_id}/stop")
    async def stop_calibration(job_id: str) -> dict[str, object]:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Calibration job not found")
        control = app.state.calibration_controls.get(job_id)
        if control is not None:
            control.set()
        return stop_calibration_state(state_path)

    @app.post("/api/process/album/{job_id}/stop")
    async def stop_album_processing(job_id: str) -> dict[str, object]:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Album processing job not found")
        control = app.state.calibration_controls.get(job_id)
        if control is not None:
            control.set()
        return stop_calibration_state(state_path)

    @app.get("/api/process/calibration/{job_id}/events")
    async def calibration_events(job_id: str) -> StreamingResponse:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Calibration job not found")

        async def stream():
            cursor = 0
            while True:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                events = state.get("events", [])
                for event in events[cursor:]:
                    yield f"event: calibration\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                cursor = len(events)
                if state.get("status") in {"complete", "failed", "stopped"}:
                    yield f"event: complete\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/process/album/{job_id}/events")
    async def album_events(job_id: str) -> StreamingResponse:
        manifest_path = current_manifest_path()
        state_path = manifest_path.parent / ".stem-comparison" / "jobs" / job_id / "status.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="Album processing job not found")

        async def stream():
            cursor = 0
            while True:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                events = state.get("events", [])
                for event in events[cursor:]:
                    yield f"event: processing\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                cursor = len(events)
                if state.get("status") in {"complete", "failed", "stopped"}:
                    yield f"event: complete\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.patch("/api/settings/{name}")
    async def patch_setting(name: str, request: Request) -> dict[str, object]:
        payload = await request.json()
        if not isinstance(payload, dict) or "value" not in payload:
            raise HTTPException(status_code=422, detail="Expected a JSON object with a value field")
        return update_setting(name, payload["value"], paths)

    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        async def frontend(path: str) -> FileResponse | JSONResponse:
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            requested = (dist / path).resolve()
            try:
                requested.relative_to(dist.resolve())
            except ValueError:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            if requested.is_file():
                return FileResponse(requested)
            index = dist / "index.html"
            return FileResponse(index) if index.exists() else JSONResponse({"detail": "Not found"}, status_code=404)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
