from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalogue import discover_catalogue
from app.config import ensure_app_paths
from app.processing import _capture_returned_outputs, _pick_instrumental, _probe_output, _production_separator_factory, _validate_returned_stems
from app.projects import _probe_with_ffprobe, normalized_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in CPU smoke for one live audio-separator Candidate.")
    parser.add_argument("--clip", required=True, type=Path, help="A short user-supplied audio clip.")
    parser.add_argument("--candidate", required=True, help="Live Candidate id, e.g. preset:instrumental_full.")
    parser.add_argument("--output-dir", type=Path, help="Destination for the validated smoke Output.")
    parser.add_argument("--confirm", action="store_true", help="Confirm model download and CPU processing.")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("This smoke is opt-in and can download a model; repeat with --confirm.")
    clip = normalized_path(args.clip)
    if not clip.is_file():
        parser.error("--clip must point to an existing user-supplied audio file.")
    source_metadata = _probe_with_ffprobe(clip)
    paths = ensure_app_paths()
    catalogue = discover_catalogue(paths=paths)
    if not catalogue.get("live"):
        parser.error(f"Live Candidate discovery failed: {catalogue.get('error')}")
    candidate = next((item for item in catalogue["candidates"] if item.get("candidateId") == args.candidate), None)
    if candidate is None:
        parser.error("--candidate must match the current live catalogue.")
    output_root = normalized_path(args.output_dir) if args.output_dir else paths.data_dir / "cpu-smoke"
    run_root = output_root / uuid.uuid4().hex[:12]
    run_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "cpu-smoke-report.jsonl"
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    stage_started = started
    try:
        import psutil

        process = psutil.Process(os.getpid())
        memory_unit = "bytes"
    except ImportError:
        process = None
        memory_unit = "bytes" if os.name == "nt" else None

    if process is None and os.name == "nt":
        import ctypes

        class _MemoryCounters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        def windows_rss() -> int | None:
            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(_MemoryCounters)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            return int(counters.WorkingSetSize) if ok else None
    else:
        windows_rss = None
    peak_rss = 0

    def sample_memory() -> int | None:
        nonlocal peak_rss
        if process is not None:
            rss = int(process.memory_info().rss)
        elif windows_rss is not None:
            rss = windows_rss()
            if rss is None:
                return None
        else:
            return None
        peak_rss = max(peak_rss, rss)
        return rss

    def cache_snapshot() -> dict[str, int]:
        if not paths.model_cache.exists():
            return {}
        return {str(item.relative_to(paths.model_cache)): item.stat().st_size for item in paths.model_cache.rglob("*") if item.is_file()}

    cache_before = cache_snapshot()
    sample_memory()
    record: dict[str, object] = {
        "startedAt": started_at,
        "candidateId": candidate["candidateId"],
        "candidateType": candidate.get("type"),
        "candidateLabel": candidate.get("label"),
        "technicalIdentifier": candidate.get("technicalIdentifier"),
        "components": candidate.get("components", []),
        "algorithm": candidate.get("algorithm"),
        "engine": catalogue["engine"],
        "source": str(clip),
        "sourceDurationSeconds": source_metadata["durationSeconds"],
        "modelCache": str(paths.model_cache),
        "outputDirectory": str(output_root),
        "status": "running",
        "memory": {"rssUnit": memory_unit, "peakRssBytes": None},
        "limitations": ["CPU-only smoke; elapsed time depends on local CPU and model download state."],
        "returnedOutputs": [],
    }
    returned_outputs: list[dict[str, object]] = []
    try:
        separator = _production_separator_factory(candidate, paths.model_cache, run_root)
        record["modelPreparationSeconds"] = round(time.perf_counter() - stage_started, 3)
        sample_memory()
        stage_started = time.perf_counter()
        returned = separator.separate(str(clip), None)
        record["separationSeconds"] = round(time.perf_counter() - stage_started, 3)
        sample_memory()
        stage_started = time.perf_counter()
        returned_outputs = _capture_returned_outputs(
            separator,
            [str(item) for item in returned],
            output_root / "returned",
            slot=args.candidate.split(":")[-1][:1].upper(),
            sequence=1,
            title=candidate.get("label") or args.candidate,
        )
        record["returnedOutputs"] = returned_outputs
        returned_outputs = _validate_returned_stems(separator, candidate, returned_outputs)
        record["returnedOutputs"] = returned_outputs
        selected = _pick_instrumental(returned_outputs)
        metadata = _probe_output(selected, float(source_metadata["durationSeconds"]), _probe_with_ffprobe)
        record["validationSeconds"] = round(time.perf_counter() - stage_started, 3)
        final = output_root / f"{candidate['candidateId'].replace(':', '-')}_Instrumental.wav"
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(selected, final)
        final_metadata = _probe_with_ffprobe(final)
        cache_after = cache_snapshot()
        record.update({
            "status": "complete",
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "totalSeconds": round(time.perf_counter() - started, 3),
            "output": str(final),
            "outputBytes": final.stat().st_size,
            "outputDurationSeconds": final_metadata["durationSeconds"],
            "outputCodec": final_metadata.get("codec"),
            "modelsDownloaded": [{"path": name, "bytes": size} for name, size in sorted(cache_after.items()) if name not in cache_before or cache_before[name] != size],
            "cacheBytesBefore": sum(cache_before.values()),
            "cacheBytesAfter": sum(cache_after.values()),
        })
        sample_memory()
        record["memory"] = {"rssUnit": memory_unit, "peakRssBytes": peak_rss or None}
        with report_path.open("a", encoding="utf-8") as report:
            report.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False))
        return 0
    except Exception as exc:
        cache_after = cache_snapshot()
        record.update({
            "status": "failed",
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "totalSeconds": round(time.perf_counter() - started, 3),
            "error": str(exc) or exc.__class__.__name__,
            "modelsDownloaded": [{"path": name, "bytes": size} for name, size in sorted(cache_after.items()) if name not in cache_before or cache_before[name] != size],
            "cacheBytesBefore": sum(cache_before.values()),
            "cacheBytesAfter": sum(cache_after.values()),
            "returnedOutputs": returned_outputs,
        })
        sample_memory()
        record["memory"] = {"rssUnit": memory_unit, "peakRssBytes": peak_rss or None}
        with report_path.open("a", encoding="utf-8") as report:
            report.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
