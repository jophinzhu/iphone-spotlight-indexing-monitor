"""Unit tests for dependency checking and error/notice prompts (Task 11.2).

Example-based (plain pytest) tests covering the error-handling acceptance
criteria of Requirement 6:

* 6.2 — missing dependency prompt: ``IndexingMonitor.check_dependencies`` names
  each missing libimobiledevice executable and includes fix guidance; returns
  an empty list when every dependency is present.
* 6.4 — diagnostic log writing: ``IndexingMonitor.log_diagnostic`` appends a
  tab-separated ``timestamp / category / detail`` record to the configured
  local diagnostic log file (records accumulate across calls).
* 6.3 — abnormal-exit error display: ``OutputDisplay.show_error`` renders the
  process-exited error reason as an error message.
* 6.5 — lock-screen prompt: ``OutputDisplay.show_notice`` renders an "unlock the
  device" prompt.

Dependency checking is driven through an injected ``which`` callable so we can
simulate present / missing executables without touching the real ``PATH``.
Diagnostic logging is pointed at a temp file via ``diagnostic_log_path``.

The 6.3 / 6.5 prompts are validated at the :class:`OutputDisplay` level (the
building blocks for these messages) because the orchestrator's ``run`` wiring is
owned by task 11.5; these tests do not call :meth:`IndexingMonitor.run`.

Design reference: ``design.md`` -> "Error Handling"; requirements 6.2, 6.3,
6.4, 6.5.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from spotlight_monitor.indexing_monitor import (
    DEFAULT_REQUIRED_EXECUTABLES,
    IndexingMonitor,
)
from spotlight_monitor.output_display import OutputDisplay


def _make_which(present: set[str]):
    """Build a fake ``which`` callable.

    Returns a path-like string for any executable name in ``present`` and
    ``None`` (i.e. not found on PATH) for everything else.
    """

    def fake_which(name: str) -> str | None:
        return f"C:\\fake\\bin\\{name}.exe" if name in present else None

    return fake_which


# -- 6.2: missing dependency prompt ------------------------------------------


def test_check_dependencies_all_present_returns_empty() -> None:
    """When every required executable resolves, no errors are reported (6.1/6.2)."""
    which = _make_which(set(DEFAULT_REQUIRED_EXECUTABLES))
    monitor = IndexingMonitor(which=which)

    assert monitor.check_dependencies() == []


def test_check_dependencies_reports_each_missing_executable_with_guidance() -> None:
    """Every missing executable is named and accompanied by fix guidance (6.2)."""
    # Only ``idevice_id`` is present; the other two are missing.
    which = _make_which({"idevice_id"})
    # Patch get_executable to return bare names (simulating no bundled dir)
    with patch("spotlight_monitor.indexing_monitor.get_executable", side_effect=lambda x: x):
        monitor = IndexingMonitor(which=which)
        errors = monitor.check_dependencies()

    assert errors  # non-empty: startup should abort
    joined = "\n".join(errors)
    # Each missing executable is named...
    assert "ideviceinfo" in joined
    assert "idevicesyslog" in joined
    # ...and the present one is NOT reported as missing.
    assert "缺少可执行文件 idevice_id" not in joined
    # Fix guidance (how to resolve) is included for the missing items.
    assert "libimobiledevice" in joined
    assert "PATH" in joined
    # One error line per missing executable, plus the USB-driver guidance note.
    assert len(errors) == 3


def test_check_dependencies_all_missing_names_all_and_adds_usb_driver_note() -> None:
    """When all executables are missing, each is named plus a USB-driver hint (6.2)."""
    which = _make_which(set())  # nothing on PATH
    with patch("spotlight_monitor.indexing_monitor.get_executable", side_effect=lambda x: x):
        monitor = IndexingMonitor(which=which)
        errors = monitor.check_dependencies()

    joined = "\n".join(errors)
    for exe in DEFAULT_REQUIRED_EXECUTABLES:
        assert exe in joined
    # USB driver guidance is surfaced when something is missing.
    assert "USB" in joined
    # One line per missing executable (3) plus the single USB-driver note.
    assert len(errors) == len(DEFAULT_REQUIRED_EXECUTABLES) + 1


# -- 6.4: diagnostic log writing ---------------------------------------------


def test_log_diagnostic_writes_record_with_category_detail_and_timestamp(
    tmp_path: Path,
) -> None:
    """A diagnostic record contains detail, category and a timestamp field (6.4)."""
    log_path = tmp_path / "diag.log"
    monitor = IndexingMonitor(
        diagnostic_log_path=log_path, which=_make_which(set())
    )

    monitor.log_diagnostic("子进程异常退出 code=1", category="STREAM")

    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    # Detail and category are present.
    assert "子进程异常退出 code=1" in content
    assert "STREAM" in content
    # Record is a single tab-separated line: timestamp \t category \t detail.
    line = content.splitlines()[0]
    fields = line.split("\t")
    assert len(fields) == 3
    timestamp, category, detail = fields
    assert category == "STREAM"
    assert detail == "子进程异常退出 code=1"
    # The first field looks like an ISO-8601 timestamp (date and time present).
    assert timestamp.count("-") >= 2  # YYYY-MM-DD
    assert "T" in timestamp           # ISO date/time separator


def test_log_diagnostic_appends_multiple_records(tmp_path: Path) -> None:
    """Successive diagnostic calls append, producing one line each (6.4)."""
    log_path = tmp_path / "diag.log"
    monitor = IndexingMonitor(
        diagnostic_log_path=log_path, which=_make_which(set())
    )

    monitor.log_diagnostic("first error", category="ERROR")
    monitor.log_diagnostic("second error", category="STREAM")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "first error" in lines[0]
    assert "second error" in lines[1]


def test_log_diagnostic_creates_parent_directories(tmp_path: Path) -> None:
    """The diagnostic log's parent directory is created if absent (6.4)."""
    log_path = tmp_path / "nested" / "logs" / "diag.log"
    monitor = IndexingMonitor(
        diagnostic_log_path=log_path, which=_make_which(set())
    )

    monitor.log_diagnostic("boom")

    assert log_path.exists()
    assert "boom" in log_path.read_text(encoding="utf-8")


# -- 6.3: abnormal-exit error display ----------------------------------------


def test_show_error_renders_process_exited_reason() -> None:
    """The abnormal-exit reason is rendered as an error message (6.3)."""
    stream = io.StringIO()
    display = OutputDisplay(stream=stream)

    display.show_error("日志采集进程异常退出（退出码 1）")

    output = stream.getvalue()
    # Rendered as an error (distinct prefix) carrying the exit reason.
    assert "错误:" in output
    assert "异常退出" in output
    assert "退出码 1" in output


# -- 6.5: lock-screen prompt -------------------------------------------------


def test_show_notice_renders_unlock_prompt() -> None:
    """A lock-screen condition prompts the user to unlock the device (6.5)."""
    stream = io.StringIO()
    display = OutputDisplay(stream=stream)

    display.show_notice("设备已锁定，请解锁设备以读取系统日志")

    output = stream.getvalue()
    assert "请解锁设备" in output
    # Rendered on its own line (newline-terminated notice).
    assert output.endswith("\n")
