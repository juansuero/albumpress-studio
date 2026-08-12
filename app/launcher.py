from __future__ import annotations

import ctypes
import os
import threading
import time
import webbrowser

import uvicorn

from .main import app


PORT = int(os.environ.get("ALBUMPRESS_PORT") or os.environ.get("STEM_COMPARISON_PORT") or "8765")
HOST = "127.0.0.1"
MUTEX_NAME = "Local\\StemComparison.LocalApplication"


def _single_instance_handle() -> int | None:
    if os.name != "nt":
        return 1
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        raise OSError("Could not create the local application mutex")
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle


def launch() -> None:
    mutex = _single_instance_handle()
    if mutex is None:
        print("AlbumPress Studio is already running on this computer.")
        return

    def open_browser() -> None:
        time.sleep(0.8)
        webbrowser.open(f"http://{HOST}:{PORT}/")

    threading.Thread(target=open_browser, daemon=True).start()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    finally:
        if os.name == "nt" and mutex:
            ctypes.windll.kernel32.ReleaseMutex(mutex)
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    launch()
