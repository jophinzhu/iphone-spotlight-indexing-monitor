"""Resolve paths to bundled libimobiledevice executables.

When running as a PyInstaller bundle, the executables are extracted to a
temporary directory (``sys._MEIPASS`` for --onefile). When running from source,
we look in the ``vendor/libimobiledevice`` directory relative to the project
root. If neither location contains the executables, we fall back to bare names
(resolved via system PATH).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundled_dir() -> Path | None:
    """Return the directory containing bundled libimobiledevice executables, or None."""
    # PyInstaller --onefile: files are extracted to sys._MEIPASS
    if hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "libimobiledevice"
        if candidate.is_dir():
            return candidate

    # PyInstaller --onedir or running next to the exe
    exe_dir = Path(sys.executable).parent
    candidate = exe_dir / "libimobiledevice"
    if candidate.is_dir():
        return candidate

    # Running from source: check vendor/ relative to project root
    project_root = Path(__file__).resolve().parent.parent.parent
    candidate = project_root / "vendor" / "libimobiledevice"
    if candidate.is_dir():
        return candidate

    return None


def get_executable(name: str) -> str:
    """Return the full path to a libimobiledevice executable, or bare name as fallback.

    Args:
        name: Executable name without extension (e.g. "idevice_id").

    Returns:
        Full path if found in the bundled directory, otherwise the bare name
        (which will be resolved via system PATH by subprocess).
    """
    bundled = _bundled_dir()
    if bundled is not None:
        exe_name = f"{name}.exe" if sys.platform == "win32" else name
        exe_path = bundled / exe_name
        if exe_path.is_file():
            return str(exe_path)
    return name


def get_bundled_env() -> dict[str, str] | None:
    """Return a modified environ with the bundled dir on PATH, or None if not bundled.

    This is used by subprocess calls so they can find DLLs next to the executables.
    """
    bundled = _bundled_dir()
    if bundled is not None:
        env = os.environ.copy()
        env["PATH"] = str(bundled) + os.pathsep + env.get("PATH", "")
        return env
    return None
