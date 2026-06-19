"""Core immutable data models and enums for the Spotlight indexing monitor.

These types are shared across all layers (I/O and pure-logic). They are
implemented as ``@dataclass(frozen=True)`` / ``Enum`` so they carry no side
effects and can be safely passed between threads and used as immutable
snapshots for rule hot-reloading.

Design reference: ``design.md`` -> "Components and Interfaces".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

__all__ = [
    "DeviceState",
    "DeviceInfo",
    "RawLogLine",
    "StreamEvent",
    "FilterRule",
    "ParseRule",
    "IndexingProgress",
    "AppConfig",
    "ExportMode",
]


class DeviceState(Enum):
    """Connection / pairing state of a USB-connected iOS device (Req 1)."""

    CONNECTED_PAIRED = "connected_paired"      # 已连接且已配对
    CONNECTED_UNPAIRED = "connected_unpaired"  # 已连接但未配对（需信任）
    LOCKED = "locked"                          # 已连接但锁屏


@dataclass(frozen=True)
class DeviceInfo:
    """Identity and state of a single iOS device (Req 1.2)."""

    udid: str
    name: str | None
    state: DeviceState


@dataclass(frozen=True)
class RawLogLine:
    """A single raw log line carrying its local receive timestamp.

    ``received_at`` is assigned at capture time and must remain unchanged as the
    line flows through filtering, parsing, display and disk writing (Req 4.5,
    7.3).
    """

    text: str
    received_at: datetime  # 本机接收时间戳（4.5、7.3）


class StreamEvent(Enum):
    """Events emitted by the log streamer (Req 2.3, 6.3)."""

    LINE = "line"
    DISCONNECTED = "disconnected"      # 2.3
    PROCESS_EXITED = "process_exited"  # 6.3


@dataclass(frozen=True)
class FilterRule:
    """A rule used to match log lines by keywords and/or a regex (Req 3)."""

    id: str
    keywords: tuple[str, ...] = ()      # 关键字集合
    pattern: str | None = None          # 可选正则
    enabled: bool = True


@dataclass(frozen=True)
class ParseRule:
    """A rule that extracts a progress value from a log line (Req 4)."""

    id: str
    pattern: str                # 含一个捕获组，捕获进度数值
    scale_max: float = 100.0    # 捕获值的量程上限，用于规范化到 0..100


@dataclass(frozen=True)
class IndexingProgress:
    """Normalized indexing progress extracted from a log line (Req 4)."""

    percent: float          # 规范化后 0..100
    source_line: str        # 对应原始日志行（4.3）
    observed_at: datetime   # 提取时间戳（4.4、4.5）


@dataclass(frozen=True)
class AppConfig:
    """User-configurable filter and parse rules (Req 5)."""

    filter_rules: tuple[FilterRule, ...] = field(default_factory=tuple)
    parse_rules: tuple[ParseRule, ...] = field(default_factory=tuple)
    case_sensitive: bool = False


class ExportMode(Enum):
    """Log export selection mode (Req 7.2)."""

    RAW = "raw"            # 全部原始日志
    FILTERED = "filtered"  # 仅过滤后日志
