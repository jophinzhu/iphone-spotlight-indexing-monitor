"""Log_Writer (I/O): write raw/filtered log lines to a user-specified file.

This component persists captured log lines to disk, preserving each line's
local receive timestamp (``received_at``), and supports exporting a sequence of
lines either in full (``RAW``) or restricted to lines that pass a
:class:`LogFilter` (``FILTERED``).

On-disk line format
-------------------
Each log line is serialized as a single text line (UTF-8, ``\\n`` terminated)::

    {received_at_isoformat}\\t{text}\\n

That is: the line's ``received_at`` timestamp in ISO 8601 format
(``datetime.isoformat()``), then a single TAB (``\\t``) separator, then the
original line text, then a newline. Using ISO 8601 + TAB lets a round-trip read
recover BOTH the text and the receive timestamp, and writing lines sequentially
preserves their original order (validated later by Property 14).

Because the separator is a TAB and the text is written last on the line, any
TABs that appear inside ``text`` are preserved on read by splitting only on the
FIRST TAB.

FILTERED export
---------------
``export(path, lines, mode)`` has no ``filter`` parameter (per the design
signature), so the :class:`LogFilter` used for ``FILTERED`` export is a
constructor dependency. When ``mode == ExportMode.FILTERED`` the writer applies
``self._log_filter.filter_stream(lines)``; if no filter was configured a
:class:`LogWriterError` is raised. ``RAW`` mode writes every input line and does
not require a filter.

Design reference: ``design.md`` -> "Log_Writer".
Requirements: 7.1, 7.2, 7.3, 7.4.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .log_filter import LogFilter
from .models import ExportMode, RawLogLine

__all__ = ["LogWriter", "LogWriterError"]


class LogWriterError(Exception):
    """Raised when a log file cannot be opened/written, or when a FILTERED
    export is requested without a configured :class:`LogFilter` (Req 7.4)."""


def _serialize(line: RawLogLine) -> str:
    """Serialize a log line to the on-disk text representation (no newline)."""
    return f"{line.received_at.isoformat()}\t{line.text}"


def _deserialize(raw: str) -> RawLogLine:
    """Parse a serialized line (without trailing newline) back into a
    :class:`RawLogLine`. Splits on the FIRST TAB so TABs inside the text are
    preserved. Provided as a convenience for round-trip reads/tests."""
    timestamp_str, _, text = raw.partition("\t")
    return RawLogLine(text=text, received_at=datetime.fromisoformat(timestamp_str))


class LogWriter:
    """Writes raw/filtered log lines to a user-specified file (Req 7).

    Supports two usage modes:

    * Streaming: ``open(path)`` -> repeated ``write(line)`` -> ``close()``
      (also usable as a context manager).
    * Batch export: ``export(path, lines, mode)``.

    The optional ``log_filter`` is used only by ``FILTERED`` exports.
    """

    def __init__(self, log_filter: LogFilter | None = None) -> None:
        self._log_filter = log_filter
        self._handle: TextIO | None = None
        self._path: Path | None = None

    # -- streaming write (open/write/close) --------------------------------

    def open(self, path: Path) -> None:
        """Open ``path`` for writing in UTF-8 text mode (Req 7.1).

        Raises :class:`LogWriterError` when the path is not writable (e.g. a
        missing parent directory, a directory in place of a file, or any other
        OS-level permission/IO failure) so callers can show an error and stop
        the save operation (Req 7.4).
        """
        path = Path(path)
        # Close any previously opened handle to avoid leaking file descriptors.
        self.close()
        try:
            handle = open(path, "w", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise LogWriterError(f"无法写入日志文件 {path}: {exc}") from exc
        self._handle = handle
        self._path = path

    def write(self, line: RawLogLine) -> None:
        """Write a single line, preserving its ``received_at`` (Req 7.1, 7.3).

        Must be called after :meth:`open`. The line is appended in arrival
        order and flushed so a concurrent reader observes it promptly.
        """
        if self._handle is None:
            raise LogWriterError("写入前必须先调用 open()")
        try:
            self._handle.write(_serialize(line) + "\n")
            self._handle.flush()
        except OSError as exc:  # pragma: no cover - rare runtime IO failure
            raise LogWriterError(f"写入日志文件失败: {exc}") from exc

    def close(self) -> None:
        """Close the underlying file handle if open. Safe to call repeatedly."""
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None
                self._path = None

    # -- context manager support -------------------------------------------

    def __enter__(self) -> "LogWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- batch export ------------------------------------------------------

    def export(
        self, path: Path, lines: Sequence[RawLogLine], mode: ExportMode
    ) -> None:
        """Export ``lines`` to ``path`` according to ``mode`` (Req 7.2).

        * ``ExportMode.RAW`` writes every input line, in order.
        * ``ExportMode.FILTERED`` writes only the lines produced by the
          configured :class:`LogFilter` (``filter_stream``), preserving order.
          A :class:`LogWriterError` is raised if no filter was configured.

        Each line is serialized with the same format as :meth:`write`, so RAW
        and FILTERED exports are byte-compatible with streamed output and can be
        round-tripped (Req 7.3).
        """
        if mode is ExportMode.FILTERED:
            if self._log_filter is None:
                raise LogWriterError(
                    "FILTERED 导出需要在构造 LogWriter 时提供 LogFilter"
                )
            out_lines: Sequence[RawLogLine] = list(
                self._log_filter.filter_stream(lines)
            )
        elif mode is ExportMode.RAW:
            out_lines = lines
        else:  # pragma: no cover - defensive, ExportMode is a closed enum
            raise LogWriterError(f"未知的导出模式: {mode!r}")

        path = Path(path)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                for line in out_lines:
                    handle.write(_serialize(line) + "\n")
        except OSError as exc:
            raise LogWriterError(f"无法写入日志文件 {path}: {exc}") from exc
