from __future__ import annotations

import json
import math
import shutil
import wave
from pathlib import Path

import pytest

import app.video_proof as proof
from app.video_render import VideoRenderError


def snapshot_fixture() -> dict:
    tracks = [
        {"trackId": "one", "sequence": 1, "title": "01-01 Short", "durationSeconds": 4, "startFrame": 0, "durationInFrames": 120, "proposedRemovalSeconds": 0.4, "audioKey": "audio-1"},
        {"trackId": "two", "sequence": 2, "title": "01-02 The Longest Proof Title / With a Subtitle", "durationSeconds": 4, "startFrame": 120, "durationInFrames": 120, "proposedRemovalSeconds": 2.5, "audioKey": "audio-2"},
        {"trackId": "three", "sequence": 3, "title": "01-03 Closing", "durationSeconds": 4, "startFrame": 240, "durationInFrames": 120, "proposedRemovalSeconds": 0.2, "audioKey": "audio-3"},
    ]
    return {
        "projectFolder": "fixture",
        "fingerprints": {"projectManifest": "manifest-a", "configuration": "config-a"},
        "codeFingerprint": "code-a",
        "assets": {"artwork": {"relativePath": "video/artwork.png", "sha256": "art-a", "bytes": 10}, "audio-1": {"relativePath": "final/one.wav", "sha256": "one-a", "bytes": 10}},
        "tracks": tracks,
        "expected": {"width": 1920, "height": 1080, "fps": 30, "frameCount": 360, "durationSeconds": 12},
        "props": {"artist": "ARTIST", "album": "Album", "colors": {"primary": "#111111"}, "cinematicFinish": "Textured", "brand": {"enabled": True}, "includeAudio": True},
    }


def write_current_proof(tmp_path: Path, *, fingerprint: str, approval: str = "pending", legacy: bool = False) -> tuple[Path, Path]:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    proof_dir = tmp_path / "video" / "proofs" / ("a" * 32)
    proof_dir.mkdir(parents=True)
    snapshot = snapshot_fixture()
    selection = proof._selection_plan(snapshot)
    manifest = {"proofId": "a" * 32, "inputFingerprint": fingerprint, "approval": {"status": approval, "inputFingerprint": fingerprint, "artifactHashes": {}}, "selection": selection, "artifacts": {}}
    if legacy:
        manifest.update({"schemaVersion": 1, "proofVersion": 1, "provenance": {"snapshot": proof._fingerprint_payload(snapshot)}})
    else:
        manifest.update({"schemaVersion": proof.PROOF_SCHEMA_VERSION, "proofVersion": proof.PROOF_SCHEMA_VERSION, "recipeVersion": proof.PROOF_RECIPE_VERSION, "provenance": {"fingerprintPayload": proof._fingerprint_payload(snapshot, selection)}})
    (proof_dir / "proof-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pointer = {"proofId": "a" * 32}
    if not legacy:
        pointer.update({"schemaVersion": proof.PROOF_SCHEMA_VERSION, "proofVersion": proof.PROOF_SCHEMA_VERSION, "recipeVersion": proof.PROOF_RECIPE_VERSION, "inputFingerprint": fingerprint})
    (tmp_path / "video" / "proofs" / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
    return project, proof_dir / "proof-manifest.json"


def test_risk_selection_is_deterministic_and_explains_tail_reason() -> None:
    snapshot = snapshot_fixture()
    first = proof._selection_plan(snapshot)
    second = proof._selection_plan(snapshot)

    assert first == second
    assert first["transition-risk"]["trackSequences"] == [2, 3]
    assert "2.500s" in first["transition-risk"]["reason"]
    assert first["long-title"]["trackSequences"] == [2]
    assert first["opening"]["frameCount"] == 240
    assert first["closing"]["startFrame"] == 120
    assert first["thumbnail"]["frame"] == proof.PROOF_FPS
    assert "stable frame" in first["thumbnail"]["reason"]


def test_input_fingerprint_changes_when_any_fingerprinted_input_changes() -> None:
    snapshot = snapshot_fixture()
    original = proof.proof_input_fingerprint(snapshot)
    changed_asset = json.loads(json.dumps(snapshot))
    changed_asset["assets"]["artwork"]["sha256"] = "art-b"
    changed_title = json.loads(json.dumps(snapshot))
    changed_title["tracks"][1]["title"] += " changed"

    assert proof.proof_input_fingerprint(changed_asset) != original
    assert proof.proof_input_fingerprint(changed_title) != original


def test_v2_selection_is_part_of_the_canonical_fingerprint() -> None:
    snapshot = snapshot_fixture()
    selection = proof._selection_plan(snapshot)
    original = proof.proof_input_fingerprint(snapshot, selection)

    thumbnail_changed = json.loads(json.dumps(selection))
    thumbnail_changed["thumbnail"]["frame"] = 31
    transition_changed = json.loads(json.dumps(selection))
    transition_changed["transition-risk"]["startFrame"] += 1

    assert proof.proof_input_fingerprint(snapshot, thumbnail_changed) != original
    assert proof.proof_input_fingerprint(snapshot, transition_changed) != original


def test_v2_render_script_hash_is_fingerprinted_but_unrelated_ui_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = snapshot_fixture()
    selection = proof._selection_plan(snapshot)
    original = proof.proof_input_fingerprint(snapshot, selection)
    real_sha256 = proof._sha256

    def changed_script_hash(path: Path) -> str:
        if Path(path).resolve() == proof.PROOF_NODE_SCRIPT.resolve():
            return "changed-render-script"
        return real_sha256(path)

    monkeypatch.setattr(proof, "_sha256", changed_script_hash)
    assert proof.proof_input_fingerprint(snapshot, selection) != original

    monkeypatch.undo()
    original = proof.proof_input_fingerprint(snapshot, selection)
    monkeypatch.setattr(proof, "_sha256", lambda path: "changed-ui" if Path(path).name == "App.tsx" else real_sha256(path))
    assert proof.proof_input_fingerprint(snapshot, selection) == original


def test_v2_fingerprint_survives_serialization_and_v1_manifest_is_readable_but_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = snapshot_fixture()
    selection = proof._selection_plan(snapshot)
    payload = proof._fingerprint_payload(snapshot, selection)
    fingerprint = proof.proof_input_fingerprint(snapshot, selection)
    restored = json.loads(json.dumps(payload))
    assert proof._digest(restored) == fingerprint

    project, _manifest = write_current_proof(tmp_path, fingerprint="legacy", approval="pending", legacy=True)
    monkeypatch.setattr(proof, "_current_snapshot", lambda _root: (snapshot, fingerprint))
    state = proof.read_current_proof_pack(project)
    assert state["status"] == "blocked"
    assert state["approval"]["status"] == "stale"
    assert any("older fingerprint contract" in issue for issue in state["issues"])


def test_approval_is_stale_after_relevant_input_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _manifest = write_current_proof(tmp_path, fingerprint="old", approval="approved", legacy=True)
    monkeypatch.setattr(proof, "_current_snapshot", lambda _root: (snapshot_fixture(), "new"))

    state = proof.read_current_proof_pack(project)

    assert state["ready"] is False
    assert state["approval"]["status"] == "stale"
    assert any("stale" in issue.casefold() for issue in state["issues"])
    with pytest.raises(proof.VideoProofError, match="stale"):
        proof.approve_current_proof(project, "a" * 32)


def test_approval_is_idempotent_and_unlocks_gate_until_input_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fingerprint = proof.proof_input_fingerprint(snapshot_fixture())
    project, _manifest = write_current_proof(tmp_path, fingerprint=fingerprint, approval="pending")
    monkeypatch.setattr(proof, "_current_snapshot", lambda _root: (snapshot_fixture(), fingerprint))

    approved = proof.approve_current_proof(project, "a" * 32)
    repeated = proof.approve_current_proof(project, "a" * 32)

    assert approved["ready"] is True
    assert approved["approval"]["status"] == "approved"
    assert repeated["approval"] == approved["approval"]
    assert proof.require_approved_proof(tmp_path)["proofId"] == "a" * 32

    monkeypatch.setattr(proof, "_current_snapshot", lambda _root: (snapshot_fixture(), "changed"))
    with pytest.raises(proof.VideoProofError, match="blocked"):
        proof.require_approved_proof(tmp_path)


def test_sustained_fast_export_is_blocked_before_proof_approval(tmp_path: Path) -> None:
    manifest = tmp_path / "project.json"
    manifest.write_text("{}", encoding="utf-8")

    from app.video_fast_export import start_fast_render

    with pytest.raises(VideoRenderError, match="blocked until"):
        start_fast_render(manifest, {}, synthetic=False)


def test_fast_export_gate_blocks_approves_then_reblocks_after_input_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fingerprint = proof.proof_input_fingerprint(snapshot_fixture())
    project, _manifest = write_current_proof(tmp_path, fingerprint=fingerprint, approval="pending")
    monkeypatch.setattr(proof, "_current_snapshot", lambda _root: (snapshot_fixture(), fingerprint))
    from app import video_fast_export as fast

    processes: dict[str, dict] = {}
    with pytest.raises(VideoRenderError, match="blocked until"):
        fast.start_fast_render(project, processes, synthetic=False)

    proof.approve_current_proof(project, "a" * 32)
    monkeypatch.setattr(fast, "_active_job", lambda _root: None)
    monkeypatch.setattr(fast, "_fast_snapshot", lambda _root, _job_id, *, synthetic: {"kind": "real-fast-album", "expected": {"frameCount": 1}})
    monkeypatch.setattr(fast, "_monitor_fast", lambda *_args: None)
    monkeypatch.setattr(fast, "_telemetry_loop", lambda *_args: None)

    class FakeProcess:
        pid = 12345
        stdout = None

    monkeypatch.setattr(fast.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    queued = fast.start_fast_render(project, processes, synthetic=False)
    assert queued["status"] == "queued"
    shutil.rmtree(fast._job_dir(tmp_path, queued["jobId"]))

    monkeypatch.setattr(proof, "_current_snapshot", lambda _root: (snapshot_fixture(), "changed"))
    with pytest.raises(VideoRenderError, match="blocked"):
        fast.start_fast_render(project, processes, synthetic=False)


def test_missing_font_fails_closed_with_relink_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"assets": {"displayFont": {"path": "video/missing.ttf"}, "utilityFont": {"path": "video/utility.woff2"}}}
    monkeypatch.setattr(proof, "build_video_state", lambda _root: {"ready": True, "config": config})
    monkeypatch.setattr(proof, "_real_snapshot", lambda _root: snapshot_fixture())

    with pytest.raises(proof.VideoProofError, match="relink or replace"):
        proof._real_proof_snapshot(tmp_path)


def test_audio_activity_rejects_silent_proof_audio(tmp_path: Path) -> None:
    tone = tmp_path / "tone.wav"
    silence = tmp_path / "silence.wav"
    for path, amplitude in ((tone, 5000), (silence, 0)):
        samples = bytearray()
        for index in range(4_800):
            value = int(amplitude * math.sin(2 * math.pi * 440 * index / 48_000)) if amplitude else 0
            samples.extend(value.to_bytes(2, "little", signed=True) * 2)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(48_000)
            handle.writeframes(bytes(samples))

    assert proof._audio_activity(tone)["maxVolumeDb"] > proof.PROOF_AUDIO_SILENCE_DB
    assert proof._audio_activity(silence)["maxVolumeDb"] <= proof.PROOF_AUDIO_SILENCE_DB


def test_cancel_does_not_promote_proof_folder(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    job_id = "b" * 32
    job_dir = tmp_path / proof.PROOF_JOB_ROOT / job_id
    staging = job_dir / "staging" / "proof"
    staging.mkdir(parents=True)
    status_path = job_dir / "status.json"
    status_path.write_text(json.dumps({"jobId": job_id, "status": "running", "stage": "rendering"}), encoding="utf-8")

    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    processes = {job_id: {"process": FakeProcess(), "statusPath": status_path, "cancelRequested": False}}
    result = proof.stop_proof_job(project, job_id, processes)

    assert result["status"] == "stopping"
    assert not (tmp_path / "video" / "proofs" / job_id).exists()


def test_restart_recovery_removes_unpromoted_staging(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    job_id = "c" * 32
    job_dir = tmp_path / proof.PROOF_JOB_ROOT / job_id
    (job_dir / "staging" / "proof").mkdir(parents=True)
    (job_dir / "status.json").write_text(json.dumps({"jobId": job_id, "status": "running", "stage": "rendering"}), encoding="utf-8")

    result = proof.read_proof_job(project, job_id, {})

    assert result["status"] == "interrupted"
    assert not (job_dir / "staging").exists()
    assert not (tmp_path / "video" / "proofs" / job_id).exists()
