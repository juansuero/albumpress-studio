from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str
    value: str | None
    detail: str
    action: str | None = None


def _command_version(command: str, args: list[str]) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0] if output else None


def _python_check() -> Check:
    version = platform.python_version()
    supported = sys.version_info >= (3, 10)
    return Check(
        key="python",
        label="Python",
        status="ready" if supported else "incompatible",
        value=version,
        detail="Supported Python runtime" if supported else "Python 3.10 or newer is required",
        action=None if supported else "Install Python 3.10 or newer and rerun setup.ps1",
    )


def _engine_check() -> Check:
    if importlib.util.find_spec("audio_separator") is None:
        return Check(
            key="audioSeparator",
            label="audio-separator",
            status="missing",
            value=None,
            detail="The installed Python API is not available",
            action='Run .\\scripts\\setup.ps1 to install the CPU extra',
        )
    try:
        module = importlib.import_module("audio_separator.separator")
        separator = getattr(module, "Separator", None)
        if separator is None:
            raise ImportError("audio_separator.separator.Separator was not found")
        version = importlib.metadata.version("audio-separator")
    except importlib.metadata.PackageNotFoundError:
        return Check(
            key="audioSeparator",
            label="audio-separator",
            status="missing",
            value=None,
            detail="The installed Python API is not available",
            action='Run .\\scripts\\setup.ps1 to install the CPU extra',
        )
    except (ImportError, ModuleNotFoundError) as exc:
        return Check(
            key="audioSeparator",
            label="audio-separator",
            status="incompatible",
            value=None,
            detail=f"The package is installed but its Python API could not be imported: {exc}",
            action="Recreate the environment with .\\scripts\\setup.ps1",
        )
    except Exception as exc:  # A broken native dependency must be visible as a failed preflight.
        return Check(
            key="audioSeparator",
            label="audio-separator",
            status="incompatible",
            value=None,
            detail=f"The Python API failed during import: {exc}",
            action="Check the environment error and rerun .\\scripts\\setup.ps1",
        )
    return Check(
        key="audioSeparator",
        label="audio-separator",
        status="ready",
        value=version,
        detail="Separator Python API imported successfully",
    )


def _tool_check(key: str, label: str, command: str, args: list[str], action: str) -> Check:
    value = _command_version(command, args)
    return Check(
        key=key,
        label=label,
        status="ready" if value else "missing",
        value=value,
        detail=f"{label} detected" if value else f"{label} was not found on PATH",
        action=None if value else action,
    )


def collect_preflight(
    *,
    python_check: Callable[[], Check] = _python_check,
    engine_check: Callable[[], Check] = _engine_check,
    command_version: Callable[[str, list[str]], str | None] = _command_version,
) -> dict[str, object]:
    python_result = python_check()
    engine_result = engine_check()

    def tool(key: str, label: str, command: str, args: list[str], action: str) -> Check:
        value = command_version(command, args)
        return Check(
            key=key,
            label=label,
            status="ready" if value else "missing",
            value=value,
            detail=f"{label} detected" if value else f"{label} was not found on PATH",
            action=None if value else action,
        )

    checks = [
        python_result,
        engine_result,
        tool("ffmpeg", "FFmpeg", "ffmpeg", ["-version"], "Install FFmpeg and add it to PATH"),
        tool("ffprobe", "FFprobe", "ffprobe", ["-version"], "Install FFprobe and add it to PATH"),
        tool("node", "Node.js", "node", ["--version"], "Install Node.js LTS and rerun setup.ps1"),
    ]
    ready = all(check.status == "ready" for check in checks)
    return {
        "ready": ready,
        "platform": platform.platform(),
        "checks": [asdict(check) for check in checks],
        "summary": (
            "Ready for CPU separation"
            if ready
            else "Setup is incomplete; resolve the checks marked missing or incompatible"
        ),
    }
