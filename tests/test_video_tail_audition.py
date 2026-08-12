from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.video_tail_audition as audition


def state_fixture() -> dict:
    tracks = [
        {"trackId": "one", "sequence": 1, "title": "01-01 One", "durationSeconds": 20.0, "fileFingerprint": "one", "proposedRemovalSeconds": 3.0},
        {"trackId": "two", "sequence": 2, "title": "01-02 Two", "durationSeconds": 18.0, "fileFingerprint": "two", "proposedRemovalSeconds": 0.0},
        {"trackId": "three", "sequence": 3, "title": "01-03 Three", "durationSeconds": 16.0, "fileFingerprint": "three", "proposedRemovalSeconds": 2.0},
    ]
    return {"ready": True, "projectFolder": "fixture", "composition": {"timeline": tracks}}


def test_tail_state_is_aligned_and_only_review_tracks_are_exposed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(audition, "build_video_state", lambda *_args, **_kwargs: state_fixture())

    state = audition.build_tail_audition_state(project)

    assert [card["trackId"] for card in state["cards"]] == ["one", "three"]
    first = state["cards"][0]
    assert first["startSeconds"] == 9.0
    assert first["currentEndSeconds"] == 20.0
    assert first["proposedEndSeconds"] == 17.0
    assert first["nextSourceUrl"].endswith("two")
    assert first["nextPreviewSeconds"] == 4.0


def test_tail_decision_persists_and_becomes_pending_when_input_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    current = state_fixture()
    monkeypatch.setattr(audition, "build_video_state", lambda *_args, **_kwargs: current)

    saved = audition.save_tail_audition_decision(project, "one", "use-proposed")
    assert saved["cards"][0]["decision"] == "use-proposed"
    raw = json.loads((tmp_path / audition.TAIL_DECISION_PATH).read_text(encoding="utf-8"))
    assert raw["decisions"]["one"]["decision"] == "use-proposed"

    changed = state_fixture()
    changed["composition"]["timeline"][0]["fileFingerprint"] = "changed"
    monkeypatch.setattr(audition, "build_video_state", lambda *_args, **_kwargs: changed)
    refreshed = audition.build_tail_audition_state(project)
    assert refreshed["cards"][0]["decision"] == "pending"


def test_tail_decision_rejects_unknown_track_or_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(audition, "build_video_state", lambda *_args, **_kwargs: state_fixture())

    with pytest.raises(audition.VideoTailAuditionError, match="must be pending"):
        audition.save_tail_audition_decision(project, "one", "accept")
    with pytest.raises(audition.VideoTailAuditionError, match="no pending tail audition"):
        audition.save_tail_audition_decision(project, "missing", "use-proposed")
