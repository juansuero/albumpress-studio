from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path


MODEL_FILENAME = "UVR-MDX-NET-Inst_HQ_5.onnx"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def file_snapshot(root: Path) -> list[dict[str, object]]:
    if not root.exists():
        return []
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        files.append({
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    return files


def output_snapshot(paths: list[str]) -> list[dict[str, object]]:
    result = []
    for raw_path in paths:
        path = Path(raw_path)
        item: dict[str, object] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            item.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
        result.append(item)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Quarantined UVR MDX CPU benchmark.")
    parser.add_argument("--clip", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()

    clip = args.clip.resolve()
    model_dir = args.model_dir.resolve()
    output_dir = args.output_dir.resolve()
    result_path = args.result.resolve()
    if not clip.is_file():
        raise FileNotFoundError(clip)
    model_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    record: dict[str, object] = {
        "status": "running",
        "modelFilename": MODEL_FILENAME,
        "backendRequested": ["CPUExecutionProvider"],
        "clip": str(clip),
        "modelDir": str(model_dir),
        "outputDir": str(output_dir),
        "modelFilesBefore": file_snapshot(model_dir),
        "outputFilesBefore": file_snapshot(output_dir),
    }

    try:
        from audio_separator.separator import Separator
        import audio_separator
        import onnxruntime

        record["audioSeparatorModule"] = str(Path(audio_separator.__file__).resolve())
        record["onnxruntimeVersion"] = onnxruntime.__version__
        record["onnxruntimeAvailableProviders"] = onnxruntime.get_available_providers()

        separator = Separator(
            log_level=logging.INFO,
            model_file_dir=str(model_dir),
            output_dir=str(output_dir),
            output_format="WAV",
            mdx_params={
                "hop_length": 1024,
                "segment_size": 256,
                "overlap": 0.25,
                "batch_size": 1,
                "enable_denoise": False,
            },
        )
        record["backendActualAfterInit"] = list(separator.onnx_execution_provider or [])
        if list(separator.onnx_execution_provider or []) != ["CPUExecutionProvider"]:
            raise RuntimeError(f"Refusing benchmark: unexpected ONNX providers {separator.onnx_execution_provider!r}")

        model_started = time.perf_counter()
        separator.load_model(model_filename=MODEL_FILENAME)
        record["modelLoadSeconds"] = round(time.perf_counter() - model_started, 3)
        record["backendActualAfterLoad"] = list(separator.onnx_execution_provider or [])
        model_instance = separator.model_instance
        record["modelRuntimeClass"] = model_instance.__class__.__name__
        record["modelRuntimeModule"] = model_instance.__class__.__module__
        record["modelParameters"] = {
            name: getattr(model_instance, name, None)
            for name in ("dim_f", "dim_t", "n_fft", "hop_length", "segment_size", "overlap", "batch_size")
        }

        separation_started = time.perf_counter()
        returned = separator.separate(str(clip), None)
        record["separationSeconds"] = round(time.perf_counter() - separation_started, 3)
        record["returnedOutputs"] = output_snapshot([str(path) for path in returned])
        record["modelFilesAfter"] = file_snapshot(model_dir)
        record["outputFilesAfter"] = file_snapshot(output_dir)
        record["status"] = "complete"
    except Exception as exc:
        record.update({"status": "failed", "error": str(exc) or exc.__class__.__name__})
        record["modelFilesAfter"] = file_snapshot(model_dir)
        record["outputFilesAfter"] = file_snapshot(output_dir)
        raise
    finally:
        record["totalSeconds"] = round(time.perf_counter() - started, 3)
        result_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
