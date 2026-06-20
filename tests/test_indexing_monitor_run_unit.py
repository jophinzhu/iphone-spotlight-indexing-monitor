"""Deterministic unit tests for IndexingMonitor.run() — task 11.5.

These tests drive the orchestrator's state machine with fully-injected fakes so
the processing loop is exercised without real hardware and, critically, without
ever blocking forever:

* the line queue is fed a fixed set of lines plus an end-of-stream sentinel,
* the reconnect window is set to 0 (instant timeout) for the terminating cases,
* a ``stop_event`` is used for the reconnect-success case.

Covered: dependency-missing abort (6.2), no-device prompt (1.3), unpaired
prompt (1.4), the happy-path pipeline with received_at preserved end-to-end
(4.5, 7.3), and automatic reconnect (2.3, 2.4).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from queue import Queue
from unittest.mock import patch

from spotlight_monitor.indexing_monitor import (
    EXIT_DEVICE_NOT_READY,
    EXIT_MISSING_DEPENDENCIES,
    EXIT_NO_DEVICE,
    EXIT_OK,
    IndexingMonitor,
)
from spotlight_monitor.models import (
    DeviceInfo,
    DeviceState,
    RawLogLine,
    StreamEvent,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConnector:
    """Returns a scripted device list on each enumerate_devices() call."""

    def __init__(self, *results: list[DeviceInfo]) -> None:
        # Each element is the list returned by one call; the last is reused
        # once the script is exhausted.
        self._results = list(results) if results else [[]]
        self._i = 0

    def enumerate_devices(self, timeout_s: float = 5.0) -> list[DeviceInfo]:
        result = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return result


class FakeDisplay:
    """Records every call so assertions can inspect what was rendered."""

    def __init__(self) -> None:
        self.devices: list[list[DeviceInfo]] = []
        self.lines: list[RawLogLine] = []
        self.progress: list[object] = []
        self.notices: list[str] = []
        self.errors: list[str] = []

    def show_devices(self, devices: list[DeviceInfo]) -> None:
        self.devices.append(devices)

    def show_log_line(self, line: RawLogLine) -> None:
        self.lines.append(line)

    def update_progress(self, progress: object) -> None:
        self.progress.append(progress)

    def show_notice(self, message: str) -> None:
        self.notices.append(message)

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class FakeStreamer:
    """Pushes scripted lines + an end-of-stream sentinel when started.

    ``on_start`` (if given) is invoked at the start of every ``start`` call with
    the current start index, letting a test stop the loop after a reconnect.
    """

    def __init__(self, lines, on_start=None) -> None:
        self._lines = list(lines)
        self._on_start = on_start
        self._callback = None
        self.start_count = 0
        self.stop_count = 0

    def on_event(self, callback) -> None:
        self._callback = callback

    def start(self, udid: str, sink: "Queue[RawLogLine]") -> None:
        self.start_count += 1
        if self._on_start is not None:
            self._on_start(self.start_count, sink)
        for line in self._lines:
            sink.put(line)
        # Signal natural end-of-stream (device unplugged / process exit).
        if self._callback is not None:
            self._callback(StreamEvent.DISCONNECTED, 1)
            self._callback(StreamEvent.PROCESS_EXITED, 1)

    def stop(self) -> None:
        self.stop_count += 1


def _present(which):
    """A which() that reports every executable as present."""
    return lambda name: f"/usr/bin/{name}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_aborts_when_dependencies_missing(tmp_path):
    """Missing executables abort startup with EXIT_MISSING_DEPENDENCIES (6.2)."""
    display = FakeDisplay()
    with patch("spotlight_monitor.indexing_monitor.get_executable", side_effect=lambda x: x):
        monitor = IndexingMonitor(
            which=lambda name: None,  # everything missing
            diagnostic_log_path=tmp_path / "diag.log",
            output_display=display,
            device_connector=FakeConnector([]),
            log_streamer=FakeStreamer([]),
        )

        code = monitor.run(device_wait_s=0.0)

    assert code == EXIT_MISSING_DEPENDENCIES
    assert display.errors  # missing items were shown


def test_run_prompts_when_no_device(tmp_path):
    """No device found -> prompt to connect + unlock, EXIT_NO_DEVICE (1.3)."""
    display = FakeDisplay()
    monitor = IndexingMonitor(
        which=_present(None),
        diagnostic_log_path=tmp_path / "diag.log",
        output_display=display,
        device_connector=FakeConnector([]),  # always empty
        log_streamer=FakeStreamer([]),
    )

    code = monitor.run(device_wait_s=0.0, poll_interval_s=0.01)

    assert code == EXIT_NO_DEVICE
    assert any("连接" in n for n in display.notices)


def test_run_prompts_when_unpaired(tmp_path):
    """An unpaired device prompts to trust the computer, EXIT_DEVICE_NOT_READY (1.4)."""
    display = FakeDisplay()
    device = DeviceInfo("UDID1", "iPhone", DeviceState.CONNECTED_UNPAIRED)
    monitor = IndexingMonitor(
        which=_present(None),
        diagnostic_log_path=tmp_path / "diag.log",
        output_display=display,
        device_connector=FakeConnector([device]),
        log_streamer=FakeStreamer([]),
    )

    code = monitor.run(device_wait_s=0.0)

    assert code == EXIT_DEVICE_NOT_READY
    assert any("信任此电脑" in n for n in display.notices)


def test_run_happy_path_processes_lines_and_preserves_timestamp(tmp_path):
    """Matching lines are shown + parsed; received_at flows through unchanged (4.5, 7.3)."""
    display = FakeDisplay()
    ts = datetime(2024, 1, 2, 3, 4, 5, 678000)
    matching = RawLogLine(text="spotlight indexing progress 42%", received_at=ts)
    non_matching = RawLogLine(text="unrelated kernel message", received_at=ts + timedelta(seconds=1))

    device = DeviceInfo("UDID1", "iPhone", DeviceState.CONNECTED_PAIRED)
    streamer = FakeStreamer([matching, non_matching])
    monitor = IndexingMonitor(
        which=_present(None),
        diagnostic_log_path=tmp_path / "diag.log",
        output_display=display,
        device_connector=FakeConnector([device]),
        log_streamer=streamer,
    )

    # reconnect_window_s=0 -> the post-stream reconnect attempt times out
    # instantly, so the loop ends deterministically.
    code = monitor.run(reconnect_window_s=0.0, poll_interval_s=0.01, device_wait_s=0.0)

    assert code == EXIT_OK
    # Only the matching line was displayed, with its ORIGINAL received_at.
    assert len(display.lines) == 1
    assert display.lines[0].received_at == ts
    # Progress was parsed and normalized to 42%.
    assert display.progress
    assert abs(display.progress[-1].percent - 42.0) < 1e-9
    assert display.progress[-1].observed_at == ts  # timestamp preserved (7.3)
    assert streamer.stop_count >= 1  # stream cleaned up


def test_run_reconnects_when_device_returns(tmp_path):
    """After a disconnect, a returning device auto-restarts the stream (2.3, 2.4)."""
    display = FakeDisplay()
    device = DeviceInfo("UDID1", "iPhone", DeviceState.CONNECTED_PAIRED)
    stop = threading.Event()

    def on_start(count, sink):
        # The second start happens after a successful reconnect: stop the loop
        # so the test terminates deterministically.
        if count >= 2:
            stop.set()

    # First start pushes one line then ends; second start (post-reconnect)
    # pushes nothing and sets stop.
    streamer = FakeStreamer(
        [RawLogLine(text="indexing progress 10%", received_at=datetime.now())],
        on_start=on_start,
    )
    # enumerate: initial (device), then reconnect poll returns the device again.
    connector = FakeConnector([device], [device])
    monitor = IndexingMonitor(
        which=_present(None),
        diagnostic_log_path=tmp_path / "diag.log",
        output_display=display,
        device_connector=connector,
        log_streamer=streamer,
    )

    code = monitor.run(
        stop_event=stop,
        reconnect_window_s=2.0,
        poll_interval_s=0.01,
        device_wait_s=0.0,
    )

    assert code == EXIT_OK
    assert streamer.start_count >= 2  # stream was restarted after reconnect
    assert any("重新连接" in n for n in display.notices)
