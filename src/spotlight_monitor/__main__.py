"""Allow running the package as a module: ``python -m spotlight_monitor``.

Also serves as a convenient single-file target for PyInstaller builds.
"""

from __future__ import annotations

import sys
import traceback


def _is_frozen() -> bool:
    """Return True when running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


if __name__ == "__main__":
    code = 1
    try:
        if _is_frozen():
            # PyInstaller treats this as a top-level script, so relative
            # imports won't work. Use absolute import instead.
            from spotlight_monitor.cli import main
        else:
            from .cli import main  # type: ignore[no-redef]
        code = main()
    except KeyboardInterrupt:
        code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if _is_frozen() and sys.stdin and sys.stdin.isatty():
            # Keep the console window open so the user can read output when
            # the .exe is launched by double-clicking. Skip when running
            # non-interactively (e.g., CI pipelines).
            input("\nPress Enter to exit...")
    raise SystemExit(code)
