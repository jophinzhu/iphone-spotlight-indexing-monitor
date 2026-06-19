"""Command-line entry point for the Spotlight indexing monitor.

This module exposes :func:`main`, which is wired up as the console-script
entry point in ``pyproject.toml`` and serves as the PyInstaller build target
(see ``[project.scripts]`` and the ``spotlight-monitor`` entry).

``main`` constructs an :class:`~spotlight_monitor.indexing_monitor.IndexingMonitor`
with real collaborators and delegates to :meth:`IndexingMonitor.run`, which
performs the dependency check, device selection, streaming and the processing
pipeline. ``run`` blocks while streaming (the intended interactive behavior);
press Ctrl+C to stop.
"""

from __future__ import annotations

import sys

from . import __version__
from .indexing_monitor import IndexingMonitor


def main(argv: list[str] | None = None) -> int:
    """Program entry point. Returns a process exit code.

    Constructs the coordinator with its default (real) collaborators and runs
    the monitoring session. ``--version`` / ``-V`` prints the version and exits.
    """
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-V", "--version"):
        print(f"spotlight-monitor {__version__}")
        return 0

    monitor = IndexingMonitor()
    try:
        return monitor.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive stop (Req 2.5)
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
