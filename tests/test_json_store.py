from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import json_store


def test_atomic_write_json_retries_transient_windows_replace_lock(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "status.json"
    real_replace = json_store.os.replace
    attempts = 0

    def replace_with_transient_lock(source: str, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "Access is denied")
        real_replace(source, destination)

    monkeypatch.setattr(json_store.os, "replace", replace_with_transient_lock)
    monkeypatch.setattr(json_store.time, "sleep", lambda _: None)

    json_store.atomic_write_json(target, {"status": "running", "progress": 0.24})

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8"))["progress"] == 0.24
    assert list(tmp_path.glob(".status.*.tmp")) == []


def test_atomic_write_json_stops_after_bounded_replace_failures(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"status":"previous"}\n', encoding="utf-8")
    attempts = 0

    def replace_with_persistent_lock(source: str, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(json_store.os, "replace", replace_with_persistent_lock)
    monkeypatch.setattr(json_store.time, "sleep", lambda _: None)

    with pytest.raises(PermissionError):
        json_store.atomic_write_json(target, {"status": "new"})

    assert attempts == json_store.ATOMIC_REPLACE_ATTEMPTS
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "previous"
    assert list(tmp_path.glob(".status.*.tmp")) == []
