from __future__ import annotations

import json
from pathlib import Path

from app.catalogue import save_candidate_slots
from app.projects import fingerprint_file, open_project
from app.validated_outputs import import_validated_outputs, invalidate_product_outputs


def test_import_validated_outputs_preserves_report_provenance_and_cache_identity(tmp_path: Path) -> None:
    source_dir = tmp_path / "Album"
    source_dir.mkdir()
    source_file = source_dir / "01 - real.wav"
    source_file.write_bytes(b"source audio")
    manifest = open_project(source_dir, probe=lambda _path: {"durationSeconds": 10.0, "codec": "pcm_s16le", "sampleRate": "44100", "channels": 2})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidate = {
        "candidateId": "preset:instrumental_full",
        "type": "Preset",
        "label": "Instrumental Full",
        "engineIdentifier": "instrumental_full",
        "technicalIdentifier": "instrumental_full",
        "components": ["model.ckpt"],
        "algorithm": "avg_wave",
    }
    catalogue = {"live": True, "engine": {"version": "0.44.5"}, "candidates": [candidate]}
    save_candidate_slots(manifest_path, {"A": candidate}, catalogue)

    smoke_output = tmp_path / "smoke.wav"
    smoke_output.write_bytes(b"validated output")
    report_path = tmp_path / "cpu-smoke-report.jsonl"
    report_path.write_text(json.dumps({
        "status": "complete",
        "candidateId": candidate["candidateId"],
        "source": str(source_file),
        "output": str(smoke_output),
        "sourceDurationSeconds": 10.0,
        "outputDurationSeconds": 10.0,
        "outputCodec": "pcm_s16le",
        "outputBytes": smoke_output.stat().st_size,
        "totalSeconds": 12.5,
        "modelsDownloaded": [],
        "memory": {"peakRssBytes": None},
    }) + "\n", encoding="utf-8")

    imported = import_validated_outputs(
        manifest_path,
        track_id=manifest["tracks"][0]["trackId"],
        slot_outputs={"A": smoke_output},
        smoke_report_path=report_path,
        catalogue=catalogue,
        probe=lambda _path: {"durationSeconds": 10.0, "codec": "pcm_s16le", "sampleRate": "44100", "channels": 2},
    )

    output = imported["outputs"][f"album:{manifest['tracks'][0]['trackId']}:A"]
    assert output["status"] == "valid"
    assert output["fileFingerprint"] == fingerprint_file(Path(output["path"]))
    assert output["provenance"]["kind"] == "imported-existing-cpu-smoke-output"
    assert output["provenance"]["reportEntry"]["totalSeconds"] == 12.5
    assert output["cacheIdentity"]
    assert imported["tasks"]["import:" + manifest["tracks"][0]["trackId"] + ":A"]["stage"] == "Complete"


def test_import_rejects_report_mismatch_without_manifest_output(tmp_path: Path) -> None:
    source_dir = tmp_path / "Album"
    source_dir.mkdir()
    source_file = source_dir / "track.wav"
    source_file.write_bytes(b"source")
    manifest = open_project(source_dir, probe=lambda _path: {"durationSeconds": 10.0})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidate = {"candidateId": "model:a", "type": "Model", "label": "A", "engineIdentifier": "a", "technicalIdentifier": "a", "components": [], "algorithm": None}
    catalogue = {"live": True, "engine": {"version": "0.44.5"}, "candidates": [candidate]}
    save_candidate_slots(manifest_path, {"A": candidate}, catalogue)
    smoke_output = tmp_path / "smoke.wav"
    smoke_output.write_bytes(b"output")
    report_path = tmp_path / "report.jsonl"
    report_path.write_text(json.dumps({"status": "complete", "candidateId": "model:other", "source": str(source_file), "output": str(smoke_output)}) + "\n", encoding="utf-8")

    try:
        import_validated_outputs(
            manifest_path,
            track_id=manifest["tracks"][0]["trackId"],
            slot_outputs={"A": smoke_output},
            smoke_report_path=report_path,
            catalogue=catalogue,
            probe=lambda _path: {"durationSeconds": 10.0},
        )
    except ValueError as exc:
        assert "No complete smoke validation" in str(exc)
    else:
        raise AssertionError("The report mismatch should have blocked import")


def test_product_invalidation_removes_selection_and_blocks_exportable_state(tmp_path: Path) -> None:
    source_dir = tmp_path / "Album"
    source_dir.mkdir()
    source_file = source_dir / "track.wav"
    source_file.write_bytes(b"source")
    manifest = open_project(source_dir, probe=lambda _path: {"durationSeconds": 10.0})
    manifest_path = Path(manifest["outputFolder"]) / "project.json"
    candidate = {"candidateId": "model:a", "type": "Model", "label": "A", "engineIdentifier": "a", "technicalIdentifier": "a", "components": [], "algorithm": None}
    catalogue = {"live": True, "engine": {"version": "0.44.5"}, "candidates": [candidate]}
    save_candidate_slots(manifest_path, {"A": candidate}, catalogue)
    smoke_root = tmp_path / "smoke"
    smoke_root.mkdir()
    smoke_output = smoke_root / "instrumental.wav"
    smoke_output.write_bytes(b"output")
    report_path = smoke_root / "report.jsonl"
    report_path.write_text(json.dumps({"status": "complete", "candidateId": "model:a", "source": str(source_file), "output": str(smoke_output), "sourceDurationSeconds": 10.0, "outputDurationSeconds": 10.0, "outputCodec": "pcm_s16le", "outputBytes": smoke_output.stat().st_size}) + "\n", encoding="utf-8")
    imported = import_validated_outputs(
        manifest_path,
        track_id=manifest["tracks"][0]["trackId"],
        slot_outputs={"A": smoke_output},
        smoke_report_path=report_path,
        catalogue=catalogue,
        probe=lambda _path: {"durationSeconds": 10.0, "codec": "pcm_s16le"},
    )
    imported_output = imported["outputs"][f"album:{manifest['tracks'][0]['trackId']}:A"]
    assert imported_output["semanticStatus"] == "pending"
    invalidated = invalidate_product_outputs(manifest_path, source_root=smoke_root, reason="Semantic stem identity is unverified or incorrect.")
    output = invalidated["outputs"][f"album:{manifest['tracks'][0]['trackId']}:A"]
    assert output["status"] == "invalid"
    assert invalidated["selections"] == {}
    assert invalidated["export"]["status"] == "invalidated"
    assert invalidated["productInvalidations"][-1]["outputIds"] == [output["outputId"]]
