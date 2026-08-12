from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

import app.video as video
import app.video_thumbnail_candidates as thumbnails
from app.projects import open_project, save_manifest
from tests.fixture_assets import make_brand_library, system_test_font, write_png


VARIANTS = [
    {"slot": "A", "override": {"layout": "control"}, "rationale": "Control preserves the approved composition."},
    {"slot": "B", "override": {"layout": "album-focus"}, "rationale": "Artist-led poster composition is the only emphasis changed."},
    {"slot": "C", "override": {"layout": "instrumental-focus"}, "rationale": "Album-led poster composition is the only emphasis changed."},
]


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)


def project_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "Source"
    source.mkdir()
    write_wav(source / "01 intro.wav")
    manifest = open_project(source, tmp_path / "Project")
    root = Path(manifest["projectFolder"])
    track = manifest["tracks"][0]
    output_id = f"album:{track['trackId']}:A"
    output_name = "A_01_intro_Instrumental.wav"
    (root / "outputs" / "album").mkdir(parents=True)
    (root / "final").mkdir()
    (root / "outputs" / "album" / output_name).write_bytes(b"valid output")
    (root / "final" / "01_intro_Instrumental.wav").write_bytes(b"valid output")
    manifest["outputs"] = {output_id: {"outputId": output_id, "trackId": track["trackId"], "slot": "A", "candidateId": "model:hq5", "path": f"outputs/album/{output_name}", "status": "valid", "semanticStatus": "confirmed", "durationSeconds": 1.0, "fileFingerprint": "fixture-fingerprint"}}
    manifest["selections"] = {track["trackId"]: {"trackId": track["trackId"], "trackTitle": track["title"], "slot": "A", "candidateId": "model:hq5", "outputId": output_id, "outputFingerprint": "fixture-fingerprint", "selectedAt": "2026-08-09T00:00:00Z"}}
    save_manifest(manifest, root)
    return root / "project.json"


def configured_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    manifest_path = project_fixture(tmp_path, monkeypatch)
    artwork = tmp_path / "fixtures" / "artwork.png"
    font = system_test_font()
    write_png(artwork, 1280, 720)
    brand_library = make_brand_library(tmp_path / "fixtures")
    monkeypatch.setattr(video, "APPROVED_ARTWORK", artwork)
    monkeypatch.setattr(video, "APPROVED_DISPLAY_FONT", font)
    monkeypatch.setattr(video, "APPROVED_UTILITY_FONT", font)
    video.configure_video(
        manifest_path,
        {
            "artist": "Fixture Artist",
            "album": "Fixture Album",
            "brand": {"enabled": True, "libraryPath": str(brand_library), "thumbnailStamp": {"enabled": True, "corner": "top-right", "widthFraction": 0.045}},
        },
    )
    return manifest_path


def test_candidate_contract_rejects_untyped_or_non_distinct_overrides() -> None:
    with pytest.raises(thumbnails.ThumbnailCandidateError, match="typed fields"):
        thumbnails._validate_variants([VARIANTS[0] | {"colors": {}}, VARIANTS[1], VARIANTS[2]])
    with pytest.raises(thumbnails.ThumbnailCandidateError, match="override is invalid"):
        thumbnails._validate_variants([VARIANTS[0], VARIANTS[1] | {"override": {"layout": "album-focus", "headline": "FULL ALBUM"}}, VARIANTS[2]])
    with pytest.raises(thumbnails.ThumbnailCandidateError, match="all three"):
        thumbnails._validate_variants([VARIANTS[0], VARIANTS[1], VARIANTS[2] | {"override": {"layout": "control"}}])


def test_candidate_render_rejects_project_overlap_before_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = configured_project(tmp_path, monkeypatch)
    monkeypatch.setattr(thumbnails.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("renderer must not start"))

    with pytest.raises(thumbnails.ThumbnailCandidateError, match="external"):
        thumbnails.render_thumbnail_candidates(manifest_path, manifest_path.parent / "video" / "candidates", VARIANTS)


def test_candidate_render_rejects_changed_protected_asset_before_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = configured_project(tmp_path, monkeypatch)
    config = json.loads((manifest_path.parent / "video" / "config.json").read_text(encoding="utf-8"))
    display_font = manifest_path.parent / config["assets"]["displayFont"]["path"]
    display_font.write_bytes(display_font.read_bytes() + b"changed")
    monkeypatch.setattr(thumbnails.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("renderer must not start"))

    with pytest.raises(thumbnails.ThumbnailCandidateError, match="not ready|changed"):
        thumbnails.render_thumbnail_candidates(manifest_path, tmp_path / "external", VARIANTS)
    assert not (tmp_path / "external").exists()


def test_candidate_render_cancelled_before_process_leaves_no_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = configured_project(tmp_path, monkeypatch)
    output = tmp_path / "external"
    monkeypatch.setattr(thumbnails.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("renderer must not start"))

    with pytest.raises(thumbnails.ThumbnailCandidateError, match="cancelled"):
        thumbnails.render_thumbnail_candidates(manifest_path, output, VARIANTS, cancelled=lambda: True)
    assert list(output.glob("*")) == []


def test_cli_accepts_candidate_set_request_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schemaVersion": 1, "candidateSetId": "cs-fixture", "variants": VARIANTS}), encoding="utf-8")
    captured = {}

    def fake_render(project_manifest, output_parent, variants):
        captured.update({"projectManifest": project_manifest, "outputParent": output_parent, "variants": variants})
        return {"renderFingerprint": "f" * 64}

    monkeypatch.setattr(thumbnails, "render_thumbnail_candidates", fake_render)
    assert thumbnails.main(["--project-manifest", "project.json", "--output-parent", "out", "--variants-json", str(request)]) == 0
    assert captured["variants"] == VARIANTS
    assert json.loads(capsys.readouterr().out)["renderFingerprint"] == "f" * 64


def test_candidate_render_produces_exact_hash_verified_abc_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = configured_project(tmp_path, monkeypatch)
    output = tmp_path / "external"

    first = thumbnails.render_thumbnail_candidates(manifest_path, output, VARIANTS)
    root = Path(first["rootPath"])
    before = {path.name: thumbnails._sha256(path) for path in root.iterdir()}
    second = thumbnails.render_thumbnail_candidates(manifest_path, output, VARIANTS)

    assert first["renderFingerprint"] == second["renderFingerprint"]
    assert {path.name for path in root.iterdir()} == {"A.png", "B.png", "C.png", "thumbnail-candidates.json"}
    assert [item["slot"] for item in first["outputs"]] == ["A", "B", "C"]
    assert len({item["sha256"] for item in first["outputs"]}) == 3
    assert all((item["width"], item["height"]) == (1280, 720) and item["bytes"] <= 2 * 1024 * 1024 for item in first["outputs"])
    assert before == {path.name: thumbnails._sha256(path) for path in root.iterdir()}
    assert first["youtubeMutated"] is False
    assert first["imageGenUsed"] is False

    (root / "unexpected.txt").write_text("ambiguous", encoding="utf-8")
    with pytest.raises(thumbnails.ThumbnailCandidateError, match="unexpected"):
        thumbnails.render_thumbnail_candidates(manifest_path, output, VARIANTS)
