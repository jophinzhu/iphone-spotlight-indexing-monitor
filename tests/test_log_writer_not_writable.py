"""Unit tests for Log_Writer save-path-not-writable handling (Task 6.4).

These example-based tests verify Requirement 7.4: WHEN the target save path is
not writable, THE Indexing_Monitor SHALL show an error and stop the save
operation. In the implementation this surfaces as :class:`LogWriter` raising
:class:`LogWriterError` (which callers catch to show the error and stop), and
as no output file being produced for the bad path.

The scenarios below are chosen to be robust cross-platform (they run on
Windows as well as POSIX) WITHOUT relying on ``chmod``/POSIX permission bits:

* opening a path inside a non-existent directory (raises ``FileNotFoundError``),
* opening a path that is actually an existing directory (raises
  ``IsADirectoryError`` / ``PermissionError``),
* exporting into a non-existent directory.

Design reference: ``design.md`` -> "Error Handling" -> "保存路径不可写"; Req 7.4.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from spotlight_monitor.log_writer import LogWriter, LogWriterError
from spotlight_monitor.models import ExportMode, RawLogLine


def _line(text: str = "spotlight indexing progress") -> RawLogLine:
    return RawLogLine(text=text, received_at=datetime(2025, 1, 1, 12, 0, 0))


def test_open_missing_parent_dir_raises_and_writes_no_file(tmp_path: Path) -> None:
    """open() on a path whose parent directory does not exist must fail.

    The missing parent makes the path unwritable, so ``open`` raises
    ``LogWriterError`` (wrapping the OSError) and the save operation stops: no
    file is created at the bad path (Req 7.4).
    """
    bad_path = tmp_path / "nonexistent_dir" / "out.log"

    writer = LogWriter()
    with pytest.raises(LogWriterError):
        writer.open(bad_path)

    # The save operation stopped: nothing was created at the unwritable path.
    assert not bad_path.exists()


def test_open_on_directory_path_raises(tmp_path: Path) -> None:
    """open() on a path that IS an existing directory must fail.

    Passing a directory where a file is expected raises an OS-level error
    (IsADirectoryError / PermissionError on Windows), which LogWriter wraps in
    ``LogWriterError`` (Req 7.4).
    """
    writer = LogWriter()
    with pytest.raises(LogWriterError):
        writer.open(tmp_path)  # tmp_path is a directory, not a file


def test_write_after_failed_open_raises(tmp_path: Path) -> None:
    """After a failed open(), the writer has no handle so write() must fail.

    This confirms the save operation is genuinely stopped: a failed open leaves
    the writer unusable and ``write`` raises ``LogWriterError`` rather than
    silently succeeding (Req 7.4).
    """
    bad_path = tmp_path / "nonexistent_dir" / "out.log"

    writer = LogWriter()
    with pytest.raises(LogWriterError):
        writer.open(bad_path)

    # No successful open => write must raise, not silently drop the line.
    with pytest.raises(LogWriterError):
        writer.write(_line())


def test_export_to_unwritable_path_raises_and_writes_no_file(tmp_path: Path) -> None:
    """export() into a non-existent directory must fail and produce no file.

    Batch export to an unwritable destination raises ``LogWriterError`` and does
    not create the target file, so the save operation stops cleanly (Req 7.4).
    """
    bad_path = tmp_path / "nonexistent_dir" / "export.log"
    lines = [_line("a"), _line("b")]

    writer = LogWriter()
    with pytest.raises(LogWriterError):
        writer.export(bad_path, lines, ExportMode.RAW)

    assert not bad_path.exists()
