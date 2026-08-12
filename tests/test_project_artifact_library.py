from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.project_artifact_library import (
    ArtifactLibraryError,
    _CLEANUP_FINGERPRINT_FIELDS,
    _canonical_fingerprint,
    cleanup_plan_file_sha256,
    ProjectArtifactLibrary,
    PROTECTED,
    REVIEW_REQUIRED,
    SAFE_TEMPORARY,
    human_media_filename,
    human_release_folder_name,
)
from app.projects import fingerprint_file


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def make_synthetic_project(tmp_path: Path) -> Path:
    root = tmp_path / "Little Songs Fixture"
    root.mkdir()
    write_json(root / "project.json", {"schemaVersion": 2, "projectId": "fixture-project", "projectName": "Little Songs Fixture", "albumName": "Little Songs", "tracks": []})
    write_json(root / "video" / "config.json", {"artist": "Colter Wall", "album": "Little Songs"})
    current = root / "video" / "packages" / "real-job-1-v1"
    current.mkdir(parents=True)
    video = current / "album-video.mp4"
    video.write_bytes(b"fixture-approved-video")
    thumb = current / "thumbnail.png"
    thumb.write_bytes(b"fixture-thumbnail")
    current_manifest = {
        "schemaVersion": 1,
        "packageId": "real-job-1-v1",
        "kind": "real-album-video-package",
        "reviewStatus": "approved",
        "current": True,
        "createdAt": "2026-08-11T00:00:00Z",
        "artifacts": {
            "albumVideo": {"path": "album-video.mp4", "bytes": video.stat().st_size, "sha256": fingerprint_file(video)},
            "thumbnail": {"path": "thumbnail.png", "bytes": thumb.stat().st_size, "sha256": fingerprint_file(thumb)},
        },
    }
    write_json(current / "manifest.json", current_manifest)
    old = root / "video" / "packages" / "real-old-v1"
    old.mkdir(parents=True)
    old_video = old / "album-video.mp4"
    old_video.write_bytes(b"old-video")
    write_json(old / "manifest.json", {"schemaVersion": 1, "packageId": "real-old-v1", "kind": "real-album-video-package", "reviewStatus": "needs-fix", "current": False, "reviewNote": "Long tails", "artifacts": {"albumVideo": {"path": "album-video.mp4", "bytes": old_video.stat().st_size, "sha256": fingerprint_file(old_video)}}})
    synthetic = root / "video" / "packages" / "synthetic-job-v1"
    synthetic.mkdir(parents=True)
    fixture = synthetic / "album-video.mp4"
    fixture.write_bytes(b"synthetic-video")
    write_json(synthetic / "manifest.json", {"schemaVersion": 1, "packageId": "synthetic-job-v1", "kind": "synthetic-video-package", "reviewStatus": "ready", "current": False, "createdAt": "2026-08-11T00:00:00Z", "artifacts": {"albumVideo": {"path": "album-video.mp4", "bytes": fixture.stat().st_size, "sha256": fingerprint_file(fixture)}}})
    duplicate = root / "video" / "renders" / "ticket-20-fast" / "v1" / "album-video-fast.mp4"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(video.read_bytes())
    write_json(duplicate.parent / "render-manifest.json", {"kind": "real-fast-album", "status": "complete", "outputPath": "video/renders/ticket-20-fast/v1/album-video-fast.mp4", "validation": {"sha256": fingerprint_file(duplicate)}})
    (root / ".stem-comparison" / "jobs").mkdir(parents=True)
    write_json(root / ".stem-comparison" / "jobs" / "active.json", {"status": "running", "jobId": "active"})
    (root / "final").mkdir()
    (root / "final" / "01.wav").write_bytes(b"source-final")
    return root


def make_video_job(root: Path, job_id: str, status: str, *, stage: str | None = None, promoted: bool = False, resumable: bool = False, corrupt: bool = False) -> Path:
    job = root / ".stem-comparison" / "video-jobs" / job_id
    job.mkdir(parents=True)
    if corrupt:
        (job / "status.json").write_text("{not-json", encoding="utf-8")
        (job / "staging" / "partial.bin").parent.mkdir(parents=True)
        (job / "staging" / "partial.bin").write_bytes(b"partial")
        return job
    write_json(job / "input.json", {"jobId": job_id, "source": "synthetic"})
    (job / "staging" / "frames" / "frame.bin").parent.mkdir(parents=True)
    (job / "staging" / "frames" / "frame.bin").write_bytes(b"frame")
    (job / "chrome-profile" / "Cache_Data" / "data.bin").parent.mkdir(parents=True)
    (job / "chrome-profile" / "Cache_Data" / "data.bin").write_bytes(b"cache")
    payload: dict[str, object] = {"jobId": job_id, "status": status, "stage": stage or status}
    if resumable:
        payload["resumable"] = True
    if promoted:
        output = root / "video" / "renders" / f"{job_id}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"verified-output")
        payload.update({"promotedPath": str(output), "validation": {"sha256": fingerprint_file(output), "bytes": output.stat().st_size, "checks": {"video": True, "duration": True}}})
    write_json(job / "status.json", payload)
    return job


def make_cleanup_project(tmp_path: Path) -> Path:
    root = tmp_path / "Cleanup Fixture"
    root.mkdir()
    write_json(root / "project.json", {"schemaVersion": 2, "projectId": "cleanup-fixture", "projectName": "Cleanup Fixture", "tracks": []})
    write_json(root / ".stem-comparison" / "jobs" / "summary.json", {"status": "complete", "jobId": "durable-summary"})
    chromium = root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "Cache_Data"
    chromium.mkdir(parents=True)
    (chromium / "data_0").write_bytes(b"regenerable-cache")
    return root


def make_verified_empty_job(root: Path, job_id: str = "empty-verified") -> Path:
    protected_output = root / "final" / "protected-output.bin"
    protected_output.parent.mkdir(parents=True, exist_ok=True)
    protected_output.write_bytes(b"protected-output")
    job = root / ".stem-comparison" / "video-jobs" / job_id
    job.mkdir(parents=True)
    write_json(job / "input.json", {"jobId": job_id, "source": "synthetic"})
    write_json(job / "status.json", {"jobId": job_id, "status": "complete", "stage": "complete", "promotedPath": str(protected_output), "validation": {"sha256": fingerprint_file(protected_output), "bytes": protected_output.stat().st_size, "checks": {"video": True, "duration": True}}})
    (job / "chrome-profile" / "Empty" / "Leaf").mkdir(parents=True)
    (job / "chrome-profile" / "Sibling").mkdir(parents=True)
    return job


def snapshot_files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_human_names_are_deterministic_and_safe() -> None:
    assert human_media_filename(artist="Colter/Wall", album="Little:Songs") == "Colter-Wall - Little-Songs - Full Album Instrumental.mp4"
    assert human_media_filename(artist="Colter Wall", album="Little Songs", state="Needs fix", note="Human review found excessive terminal tails") == "Colter Wall - Little Songs - Needs Fix.mp4"
    assert human_release_folder_name(date="2026-08-11", state="Approved", album="Little Songs") == "2026-08-11 - Approved - Full Album Instrumental"
    assert human_release_folder_name(date="bad", state="Needs fix", album="Little Songs", note="Long tails", collision_index=2).endswith("(2)")


def test_identical_previews_have_identical_plan_identity_and_do_not_write_project(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    before = snapshot_files(root)
    first = library.plan_layout_migration()
    second = ProjectArtifactLibrary(root).plan_layout_migration()
    assert first["migrationId"] == second["migrationId"]
    assert first["planFingerprint"] == second["planFingerprint"]
    assert first["sourceProjectManifestSha256"] == second["sourceProjectManifestSha256"]
    assert first["mappings"] == second["mappings"]
    assert first["releases"] == second["releases"]
    assert first["bytes"] == second["bytes"]
    assert snapshot_files(root) == before


def test_serialized_plan_and_process_restart_preserve_identity(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    loaded = json.loads(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    assert loaded["migrationId"] == plan["migrationId"]
    assert loaded["planFingerprint"] == plan["planFingerprint"]
    code = "import json,sys; from app.project_artifact_library import ProjectArtifactLibrary; p=ProjectArtifactLibrary(sys.argv[1]).plan_layout_migration(); print(json.dumps({'migrationId':p['migrationId'],'planFingerprint':p['planFingerprint']}))"
    process = subprocess.run([sys.executable, "-c", code, str(root)], cwd=Path.cwd(), capture_output=True, text=True, check=True)
    restarted = json.loads(process.stdout)
    assert restarted == {"migrationId": plan["migrationId"], "planFingerprint": plan["planFingerprint"]}


def test_semantic_changes_create_new_plan_identity(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    original = library.plan_layout_migration()
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    project["revision"] = 1
    write_json(root / "project.json", project)
    changed_manifest = ProjectArtifactLibrary(root).plan_layout_migration()
    assert changed_manifest["migrationId"] != original["migrationId"]
    assert changed_manifest["planFingerprint"] != original["planFingerprint"]
    current_video = root / "video" / "packages" / "real-job-1-v1" / "album-video.mp4"
    current_video.write_bytes(current_video.read_bytes() + b"changed-source")
    changed_source = ProjectArtifactLibrary(root).plan_layout_migration()
    assert changed_source["migrationId"] != changed_manifest["migrationId"]
    assert changed_source["planFingerprint"] != changed_manifest["planFingerprint"]


def test_saved_complete_plan_is_applicable_and_summary_is_not(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    summary = {key: plan[key] for key in ("status", "migrationId", "planFingerprint", "mappings")}
    with pytest.raises(ArtifactLibraryError):
        library.apply_layout_migration(summary, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"])
    plan_path = tmp_path / "approved-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    loaded = json.loads(plan_path.read_text(encoding="utf-8"))
    result = library.apply_layout_migration(loaded, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"])
    assert result["status"] == "promoted"


def test_video_job_classification_uses_state_and_file_role(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    make_video_job(root, "active", "running")
    make_video_job(root, "resumable", "interrupted", stage="resumable", resumable=True)
    make_video_job(root, "promoted", "complete", promoted=True)
    make_video_job(root, "unpromoted", "complete")
    make_video_job(root, "failed", "failed")
    make_video_job(root, "corrupt", "failed", corrupt=True)
    library = ProjectArtifactLibrary(root)
    by_path = {item["path"]: item for item in library.inventory(verify_hashes=True)["artifacts"]}

    assert by_path[".stem-comparison/video-jobs/active/staging/frames/frame.bin"]["category"] == PROTECTED
    assert by_path[".stem-comparison/video-jobs/active/chrome-profile/Cache_Data/data.bin"]["category"] == SAFE_TEMPORARY
    assert by_path[".stem-comparison/video-jobs/resumable/staging/frames/frame.bin"]["category"] == PROTECTED
    assert by_path[".stem-comparison/video-jobs/promoted/staging/frames/frame.bin"]["category"] == SAFE_TEMPORARY
    assert by_path[".stem-comparison/video-jobs/promoted/status.json"]["category"] == PROTECTED
    assert by_path[".stem-comparison/video-jobs/unpromoted/staging/frames/frame.bin"]["category"] == REVIEW_REQUIRED
    assert by_path[".stem-comparison/video-jobs/failed/status.json"]["category"] == REVIEW_REQUIRED
    assert by_path[".stem-comparison/video-jobs/corrupt/staging/partial.bin"]["category"] == REVIEW_REQUIRED
    assert by_path[".stem-comparison/video-jobs/promoted/staging/frames/frame.bin"]["promotionVerified"] is True


def test_inventory_resolves_current_and_classifies_protected_safe_and_review(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    inventory = library.inventory(verify_hashes=True)
    by_path = {item["path"]: item for item in inventory["artifacts"]}
    assert library.resolve_current_release()["releaseId"] == "real-job-1-v1"
    assert by_path["final/01.wav"]["category"] == PROTECTED
    assert by_path["video/packages/synthetic-job-v1/album-video.mp4"]["category"] == SAFE_TEMPORARY
    assert by_path["video/packages/real-old-v1/album-video.mp4"]["category"] == REVIEW_REQUIRED
    assert by_path["video/renders/ticket-20-fast/v1/album-video-fast.mp4"]["category"] == SAFE_TEMPORARY
    assert inventory["reclaimableBytes"] > 0


def test_layout_migration_is_atomic_idempotent_and_rolls_back(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    assert plan["status"] == "planned"
    assert any(item["destinationRelative"].startswith("video/releases/2026-08-11 - Approved") for item in plan["mappings"])
    synthetic_mapping = next(item for item in plan["mappings"] if item["sourceRelative"].endswith("synthetic-job-v1/album-video.mp4"))
    assert synthetic_mapping["destinationRelative"].endswith("video/proofs/2026-08-11 - Ready for review - Synthetic Export Smoke 01/Synthetic Export Smoke 01.mp4")
    assert plan["pathChecks"]["collisions"] == []
    result = library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"])
    assert result["status"] == "promoted"
    assert not (root / "video" / "packages" / "real-job-1-v1").exists()
    current = library.resolve_current_release()
    assert current["layout"] == "new"
    assert (root / current["folder"] / "Colter Wall - Little Songs - Full Album Instrumental.mp4").read_bytes() == b"fixture-approved-video"
    current_flags = [json.loads(path.read_text(encoding="utf-8"))["current"] for path in (root / "video" / "releases").glob("*/manifest.json")]
    assert current_flags.count(True) == 1
    assert library.plan_layout_migration()["status"] == "already_migrated"
    rolled = library.rollback_layout_migration(plan["migrationId"])
    assert rolled["status"] == "rolled-back"
    assert (root / "video" / "packages" / "real-job-1-v1" / "album-video.mp4").read_bytes() == b"fixture-approved-video"
    assert not (root / "video" / "releases" / "2026-08-11 - Approved - Full Album Instrumental").exists()


def test_cancel_and_recover_leaves_source_then_completes_after_interruption(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    with pytest.raises(ArtifactLibraryError, match="cancelled"):
        library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"], should_cancel=lambda: True)
    assert (root / "video" / "packages" / "real-job-1-v1" / "album-video.mp4").is_file()
    assert library.recover_layout_migration(plan["migrationId"])["originalUnchanged"] is True

    plan = library.plan_layout_migration()
    with pytest.raises(ArtifactLibraryError, match="interrupted"):
        library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"], interrupt_after_promotion=True)
    recovered = library.recover_layout_migration(plan["migrationId"])
    assert recovered["status"] == "promoted"
    assert not (root / "video" / "packages" / "real-job-1-v1").exists()


def test_cleanup_requires_unchanged_plan_and_rejects_traversal(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    assert plan["reclaimableBytes"] > 0
    (root / "unregistered.txt").write_text("changed filesystem", encoding="utf-8")
    with pytest.raises(ArtifactLibraryError, match="filesystem changed"):
        library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    fresh = library.plan_cleanup(verify_hashes=True)
    fresh["targets"] = [{"path": "../outside.txt", "bytes": 1, "sha256": "bad"}]
    fresh["reclaimableBytes"] = 1
    fresh["planFingerprint"] = _canonical_fingerprint(fresh, _CLEANUP_FINGERPRINT_FIELDS)
    with pytest.raises(ArtifactLibraryError, match="traverse"):
        library.apply_cleanup(fresh, confirm_fingerprint=fresh["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(fresh))


@pytest.mark.parametrize("confirmation", [None, "", "wrong-fingerprint"])
def test_cleanup_requires_exact_confirmation_before_any_mutation(tmp_path: Path, confirmation: str | None) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError):
        library.apply_cleanup(plan, confirm_fingerprint=confirmation, confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert snapshot_files(root) == before


@pytest.mark.parametrize("file_confirmation", [None, "", "wrong-file-sha"])
def test_cleanup_requires_exact_plan_file_sha_before_any_mutation(tmp_path: Path, file_confirmation: str | None) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError, match="file SHA-256"):
        library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=file_confirmation)
    assert snapshot_files(root) == before


def test_cleanup_rejects_mutated_plan_with_old_fingerprint_and_accepts_exact_plan(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    mutated = deepcopy(plan)
    mutated["targets"][0]["bytes"] += 1
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError, match="canonical"):
        library.apply_cleanup(mutated, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(mutated))
    assert snapshot_files(root) == before
    result = library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert result["status"] == "completed"


def test_cleanup_rejects_absent_or_incomplete_plan_before_any_mutation(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError):
        library.apply_cleanup({}, confirm_fingerprint="anything", confirm_plan_file_sha256="anything")
    assert snapshot_files(root) == before


def test_cleanup_prunes_deep_chromium_tree_but_keeps_durable_roots(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    assert plan["prunableDirectories"] == [
        ".stem-comparison/cache/chromium/Profile/Cache_Data",
        ".stem-comparison/cache/chromium/Profile",
        ".stem-comparison/cache/chromium",
        ".stem-comparison/cache",
    ]

    result = library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert result["prunedDirectories"] == plan["prunableDirectories"]
    assert not (root / ".stem-comparison" / "cache").exists()
    assert (root / ".stem-comparison").is_dir()
    assert (root / ".stem-comparison" / "jobs" / "summary.json").is_file()
    audit = (root / ".stem-comparison" / "audit" / "cleanup.jsonl").read_text(encoding="utf-8")
    assert "prunableDirectories" not in audit
    assert ".stem-comparison/cache" in audit


def test_cleanup_completed_job_prunes_staging_but_preserves_two_durable_summaries(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    job = make_video_job(root, "completed", "complete", promoted=True)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    assert ".stem-comparison/video-jobs/completed/staging" in plan["prunableDirectories"]
    assert ".stem-comparison/video-jobs/completed" not in plan["prunableDirectories"]

    result = library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert result["status"] == "completed"
    assert (job / "input.json").is_file()
    assert (job / "status.json").is_file()
    assert not (job / "staging").exists()
    assert not (job / "chrome-profile").exists()


def test_cleanup_does_not_prune_directory_containing_review_required_file(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    directory = root / "video" / "renders" / "reviewed"
    directory.mkdir(parents=True)
    output = directory / "safe-output.mp4"
    output.write_bytes(b"safe-output")
    review = directory / "render-manifest.json"
    write_json(review, {"kind": "render", "status": "complete", "outputPath": "video/renders/reviewed/safe-output.mp4", "validation": {"sha256": fingerprint_file(output)}})
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    assert "video/renders/reviewed" not in plan["prunableDirectories"]
    assert all(target["path"] != "video/renders/reviewed/render-manifest.json" for target in plan["targets"])


def test_cleanup_unexpected_file_between_preview_and_apply_invalidates_without_mutation(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    unexpected = root / ".stem-comparison" / "cache" / "chromium" / "unexpected.lock"
    unexpected.write_bytes(b"unexpected")
    before_target = (root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "Cache_Data" / "data_0").read_bytes()
    with pytest.raises(ArtifactLibraryError, match="filesystem changed"):
        library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert unexpected.is_file()
    assert (root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "Cache_Data" / "data_0").read_bytes() == before_target


def test_cleanup_pruning_skips_unexpected_file_without_failing_after_target_removal(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    target = root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "Cache_Data" / "data_0"
    target.unlink()
    unexpected = root / ".stem-comparison" / "cache" / "chromium" / "unexpected.lock"
    unexpected.write_bytes(b"unexpected")
    removed, skipped = library._prune_empty_directories(plan["prunableDirectories"])
    assert removed == [".stem-comparison/cache/chromium/Profile/Cache_Data", ".stem-comparison/cache/chromium/Profile"]
    assert {item["path"] for item in skipped} == {".stem-comparison/cache/chromium", ".stem-comparison/cache"}
    assert unexpected.is_file()


def test_cleanup_includes_preexisting_empty_leaf_chain_and_empty_sibling(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    empty = root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "EmptySibling" / "Leaf"
    empty.mkdir(parents=True)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    assert plan["targets"]
    assert ".stem-comparison/cache/chromium/Profile/EmptySibling/Leaf" in plan["prunableDirectories"]
    assert ".stem-comparison/cache/chromium/Profile/EmptySibling" in plan["prunableDirectories"]
    assert ".stem-comparison/cache/chromium/Profile" in plan["prunableDirectories"]


def test_cleanup_directory_only_includes_empty_job_subtree_but_not_root_or_summaries(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    (root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "Cache_Data" / "data_0").unlink()
    make_verified_empty_job(root)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    expected = {
        ".stem-comparison/video-jobs/empty-verified/chrome-profile/Empty/Leaf",
        ".stem-comparison/video-jobs/empty-verified/chrome-profile/Empty",
        ".stem-comparison/video-jobs/empty-verified/chrome-profile/Sibling",
    }
    assert plan["targets"] == []
    assert set(plan["prunableDirectories"]) == expected
    assert ".stem-comparison/video-jobs/empty-verified" not in plan["prunableDirectories"]
    assert ".stem-comparison/video-jobs/empty-verified/chrome-profile" not in plan["prunableDirectories"]


def test_cleanup_directory_only_applies_without_file_targets_on_fixture(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    (root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "Cache_Data" / "data_0").unlink()
    job = make_verified_empty_job(root)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    result = library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert result["deleted"] == []
    assert len(result["prunedDirectories"]) == 3
    assert job.is_dir()
    assert (job / "input.json").is_file()
    assert (job / "status.json").is_file()
    assert (root / "final" / "protected-output.bin").is_file()


def test_cleanup_does_not_prune_subtree_with_review_required_content(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    reviewed = root / "video" / "renders" / "reviewed"
    reviewed.mkdir(parents=True)
    safe_output = reviewed / "safe-output.mp4"
    safe_output.write_bytes(b"safe-output")
    write_json(reviewed / "render-manifest.json", {"kind": "render", "status": "complete", "outputPath": "video/renders/reviewed/safe-output.mp4", "validation": {"sha256": fingerprint_file(safe_output)}})
    (reviewed / "review-required.bin").write_bytes(b"review")
    (reviewed / "empty" / "leaf").mkdir(parents=True)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    assert all(not path.startswith("video/renders/reviewed") for path in plan["prunableDirectories"])


def test_cleanup_directory_plan_invalidates_when_content_is_created_after_preview(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    empty = root / ".stem-comparison" / "cache" / "chromium" / "Profile" / "EmptySibling" / "Leaf"
    empty.mkdir(parents=True)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    (empty / "created-after-preview.bin").write_bytes(b"late")
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError, match="filesystem changed"):
        library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert snapshot_files(root) == before


def test_cleanup_reports_unplanned_empty_descendant_precisely(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    parent = root / ".stem-comparison" / "cache" / "empty-root"
    (parent / "empty-child").mkdir(parents=True)
    library = ProjectArtifactLibrary(root)
    removed, skipped = library._prune_empty_directories([".stem-comparison/cache/empty-root"])
    assert removed == []
    assert skipped == [{"path": ".stem-comparison/cache/empty-root", "reason": "unplanned-empty-descendant"}]


def test_cleanup_pruning_rejects_symlinked_tree(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    link = root / ".stem-comparison" / "cache" / "chromium" / "linked-profile"
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    library = ProjectArtifactLibrary(root)
    with pytest.raises(ArtifactLibraryError, match="Symlink/junction"):
        library.plan_cleanup(verify_hashes=True)


def test_cleanup_pruning_rejects_symlink_inside_completed_safe_subtree(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    job = make_verified_empty_job(root)
    outside = tmp_path / "outside-job-profile"
    outside.mkdir()
    link = job / "chrome-profile" / "linked-cache"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    library = ProjectArtifactLibrary(root)
    with pytest.raises(ArtifactLibraryError, match="Symlink/junction"):
        library.plan_cleanup(verify_hashes=True)


def test_cleanup_retry_with_fresh_empty_plan_is_idempotent_and_keeps_durable_roots(tmp_path: Path) -> None:
    root = make_cleanup_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_cleanup(verify_hashes=True)
    first = library.apply_cleanup(plan, confirm_fingerprint=plan["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(plan))
    assert first["status"] == "completed"
    retry = library.plan_cleanup(verify_hashes=True)
    assert retry["targets"] == []
    assert retry["prunableDirectories"] == []
    second = library.apply_cleanup(retry, confirm_fingerprint=retry["planFingerprint"], confirm_plan_file_sha256=cleanup_plan_file_sha256(retry))
    assert second["status"] == "completed"
    assert (root / ".stem-comparison").is_dir()
    assert (root / ".stem-comparison" / "jobs").is_dir()
    assert (root / "project.json").is_file()


@pytest.mark.parametrize("kwargs", [{"confirm_fingerprint": None}, {"confirm_fingerprint": ""}, {"confirm_fingerprint": "wrong"}, {"expected_migration_id": "wrong"}])
def test_migration_requires_exact_fingerprint_and_id_before_any_mutation(tmp_path: Path, kwargs: dict[str, object]) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    arguments = {"confirm_fingerprint": plan["planFingerprint"], "expected_migration_id": plan["migrationId"]}
    arguments.update(kwargs)
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError):
        library.apply_layout_migration(plan, **arguments)
    assert snapshot_files(root) == before
    assert not (root / ".stem-comparison" / "work" / "video").exists()


def test_migration_rejects_mutated_plan_with_old_fingerprint_before_writing_work(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    mutated = deepcopy(plan)
    mutated["mappings"][0]["bytes"] += 1
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError, match="fingerprint"):
        library.apply_layout_migration(mutated, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"])
    assert snapshot_files(root) == before
    assert not (root / ".stem-comparison" / "work" / "video").exists()


@pytest.mark.parametrize("plan_factory", [lambda plan: {}, lambda plan: {key: value for key, value in plan.items() if key != "legacyManifests"}])
def test_migration_rejects_absent_or_incomplete_plan_before_writing_work(tmp_path: Path, plan_factory: object) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    rejected = plan_factory(plan)  # type: ignore[operator]
    before = snapshot_files(root)
    with pytest.raises(ArtifactLibraryError):
        library.apply_layout_migration(rejected, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"])
    assert snapshot_files(root) == before
    assert not (root / ".stem-comparison" / "work" / "video").exists()


@pytest.mark.parametrize("failure", ["one", "several", "before-switch"])
def test_partial_promotion_recovery_removes_only_verified_destinations_and_allows_retry(tmp_path: Path, failure: str) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    kwargs = {"fail_after_promotions": 1 if failure == "one" else 2} if failure in {"one", "several"} else {"fail_before_manifest_switch": True}
    with pytest.raises(ArtifactLibraryError, match="recovery"):
        library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"], **kwargs)
    assert (root / "video" / "packages" / "real-job-1-v1" / "album-video.mp4").is_file()
    recovered = library.recover_layout_migration(plan["migrationId"])
    assert recovered["status"] == "cancelled"
    assert recovered["originalUnchanged"] is True
    assert not any((root / release["destinationFolder"]).exists() for release in plan["releases"])
    retry = library.plan_layout_migration()
    library.apply_layout_migration(retry, confirm_fingerprint=retry["planFingerprint"], expected_migration_id=retry["migrationId"])
    assert library.resolve_current_release()["layout"] == "new"


def test_recovery_removes_only_durably_journaled_inflight_destination_and_allows_retry(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    with pytest.raises(ArtifactLibraryError, match="post-move"):
        library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"], interrupt_after_destination_move=1)
    state = json.loads((root / ".stem-comparison" / "work" / "video" / plan["migrationId"] / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "promoting"
    assert state["promotedDestinations"] == []
    assert state["promotionInFlight"] == plan["releases"][0]["destinationFolder"]
    assert (root / "video" / "packages" / "real-job-1-v1" / "album-video.mp4").is_file()
    recovered = library.recover_layout_migration(plan["migrationId"])
    assert recovered["removedDestinations"] == [plan["releases"][0]["destinationFolder"]]
    assert not any((root / release["destinationFolder"]).exists() for release in plan["releases"])
    retry = library.plan_layout_migration()
    assert library.apply_layout_migration(retry, confirm_fingerprint=retry["planFingerprint"], expected_migration_id=retry["migrationId"])["status"] == "promoted"


def test_recovery_after_manifest_switch_finishes_source_removal(tmp_path: Path) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    with pytest.raises(ArtifactLibraryError, match="interrupted"):
        library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"], interrupt_after_manifest_switch=True)
    recovered = library.recover_layout_migration(plan["migrationId"])
    assert recovered["status"] == "promoted"
    assert not (root / "video" / "packages" / "real-job-1-v1").exists()
    assert library.resolve_current_release()["layout"] == "new"


@pytest.mark.parametrize("alteration", ["unexpected", "modified"])
def test_partial_recovery_fails_closed_on_unexpected_or_modified_destination(tmp_path: Path, alteration: str) -> None:
    root = make_synthetic_project(tmp_path)
    library = ProjectArtifactLibrary(root)
    plan = library.plan_layout_migration()
    with pytest.raises(ArtifactLibraryError, match="recovery"):
        library.apply_layout_migration(plan, confirm_fingerprint=plan["planFingerprint"], expected_migration_id=plan["migrationId"], fail_after_promotions=1)
    first_destination = root / plan["releases"][0]["destinationFolder"]
    if alteration == "unexpected":
        altered = first_destination / "unexpected.bin"
        altered.write_bytes(b"do not delete")
    else:
        altered = next(path for path in first_destination.rglob("*") if path.is_file() and path.name != "manifest.json")
        altered.write_bytes(altered.read_bytes() + b"modified")
    with pytest.raises(ArtifactLibraryError):
        library.recover_layout_migration(plan["migrationId"])
    assert altered.is_file()
    assert (root / ".stem-comparison" / "work" / "video" / plan["migrationId"] / "state.json").is_file()
