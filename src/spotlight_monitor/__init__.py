"""spotlight_monitor: read an iPhone's live system log over USB and surface
Spotlight indexing progress on Windows.

This package follows a layered design that separates the side-effecting I/O
layer (device detection, log streaming, display, disk writing) from the pure
logic layer (filtering, parsing, configuration). Core shared data models live
in :mod:`spotlight_monitor.models`.
"""

from __future__ import annotations

from .models import (
    AppConfig,
    DeviceInfo,
    DeviceState,
    ExportMode,
    FilterRule,
    IndexingProgress,
    ParseRule,
    RawLogLine,
    StreamEvent,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AppConfig",
    "DeviceInfo",
    "DeviceState",
    "ExportMode",
    "FilterRule",
    "IndexingProgress",
    "ParseRule",
    "RawLogLine",
    "StreamEvent",
]
