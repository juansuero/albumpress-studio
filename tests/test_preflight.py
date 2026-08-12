from __future__ import annotations

from app.preflight import Check, collect_preflight


def check(key: str, status: str) -> Check:
    return Check(key, key, status, "1.0", "test", None)


def test_preflight_is_ready_when_all_dependencies_are_healthy() -> None:
    result = collect_preflight(
        python_check=lambda: check("python", "ready"),
        engine_check=lambda: check("audioSeparator", "ready"),
        command_version=lambda _command, _args: "ok",
    )
    assert result["ready"] is True
    assert [item["status"] for item in result["checks"]] == ["ready"] * 5


def test_preflight_explains_missing_engine() -> None:
    result = collect_preflight(
        python_check=lambda: check("python", "ready"),
        engine_check=lambda: check("audioSeparator", "missing"),
        command_version=lambda _command, _args: "ok",
    )
    assert result["ready"] is False
    assert next(item for item in result["checks"] if item["key"] == "audioSeparator")["status"] == "missing"


def test_preflight_exposes_incompatible_python_and_tools() -> None:
    result = collect_preflight(
        python_check=lambda: check("python", "incompatible"),
        engine_check=lambda: check("audioSeparator", "ready"),
        command_version=lambda command, _args: None if command == "node" else "ok",
    )
    assert result["ready"] is False
    assert {item["status"] for item in result["checks"]} == {"ready", "incompatible", "missing"}
