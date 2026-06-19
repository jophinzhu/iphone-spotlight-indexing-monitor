"""Allow running the package as a module: ``python -m spotlight_monitor``.

Also serves as a convenient single-file target for PyInstaller builds.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
