"""Integration tests for :class:`LogStreamer` using a fake subprocess (Task 8.3).

These exercise the streamer's I/O boundary end to end with a controllable fake
process injected via ``process_factory`` (no real ``idevicesyslog``). The reader
runs in a background thread, so assertions synchronize on the line/event queues
with modest timeouts rather than sleeping.

Scenarios covered:
  * Start + line-by-line read in arrival order, each line timestamped
    (Req 2.1, 2.2).
  * Disconnect notification + abnormal (non-zero) exit-code capture on natural
    EOF (Req 2.3, 6.3).
  * Stop terminates the subprocess, joins the reader thread and suppresses
    lifecycle events; idempotent and safe when never started (Req 2.5).
"""

from __future__ import annotations

import threading
from datetime import datetime
from queue import Empty, Queue

import pytest

from spotlight_monitor.log_streamer import LogStreamer
from spotlight_monitor.models import RawLogLine, StreamEvent

# Keep the suite fast: short timeouts are a safety net, not the happy path.
_TIMEOUT_S = 5.0


class FakeProcess:
    """A controllable stand-in for ``subprocess.Popen`` (a :class:`ProcessLike`).

    ``stdout`` is a generator that yields the predefined ``lines`` and then,
    depending on ``block_until_terminated``, either returns immediately
    (simulating the process exiting on its own / EOF) or blocks until
    :meth:`terminate` / :meth:`kill` is invoked (simulating a process that stays
    alive until the streamer stops it).

    ``poll`` / ``wait`` report ``returncode`` once the stdout iterator has ended.
    """

    def __init__(
        self,
        lines: list[str],
        *,
        returncode: int = 0,
        block_until_terminated: bool = False,
    ) -> None:
        self.returncode = returncode
        self._lines = list(lines)
        self._block = block_until_terminated
        self._terminate_event = threading.Event()
        self._exited = threading.Event()
        self.terminate_called = False
        self.kill_called = False
        self.stdout = self._generate()

    def _generate(self):
        try:
            for line in self._lines:
                if self._terminate_event.is_set():
                    break
                yield line
            if self._block:
                # Stay alive until the streamer terminates us.
                self._terminate_event.wait()
        finally:
            # Reaching here means EOF: the "process" has exited.
            self._exited.set()

    def poll(self) -> int | None:
        return self.returncode if self._exited.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._exited.wait(timeout):
            raise TimeoutError("fake process did not exit in time")
        return self.returncode

    def terminate(self) -> None:
        self.terminate_called = True
        self._terminate_event.set()

    def kill(self) -> None:
        self.kill_called = True
        self._terminate_event.set()


def _factory_for(process: FakeProcess):
    """Return a ``process_factory`` that always yields ``process``."""

    def factory(cmd: list[str]) -> FakeProcess:
        # The command is built as [executable, "-u", udid]; sanity-check shape.
        assert cmd[-2] == "-u"
        return process

    return factory


def _drain(sink: "Queue[RawLogLine]", count: int) -> list[RawLogLine]:
    """Pull ``count`` lines off ``sink`` with a per-item timeout."""
    out: list[RawLogLine] = []
    for _ in range(count):
        out.append(sink.get(timeout=_TIMEOUT_S))
    return out


# ---------------------------------------------------------------------------
# Scenario 1: start + line-by-line read, in order, timestamped (Req 2.1, 2.2)
# ---------------------------------------------------------------------------


def test_start_reads_lines_in_arrival_order_with_timestamps():
    process = FakeProcess(["a", "b", "c"], returncode=0)
    streamer = LogStreamer(process_factory=_factory_for(process))
    sink: "Queue[RawLogLine]" = Queue()

    streamer.start("UDID-1", sink)
    try:
        lines = _drain(sink, 3)
    finally:
        streamer.stop()

    assert [ln.text for ln in lines] == ["a", "b", "c"]
    for ln in lines:
        assert isinstance(ln, RawLogLine)
        assert isinstance(ln.received_at, datetime)


# ---------------------------------------------------------------------------
# Scenario 2: disconnect notice + abnormal exit-code capture (Req 2.3, 6.3)
# ---------------------------------------------------------------------------


def test_natural_exit_emits_disconnected_then_process_exited_with_code():
    process = FakeProcess(["only-line"], returncode=1)
    streamer = LogStreamer(process_factory=_factory_for(process))
    sink: "Queue[RawLogLine]" = Queue()

    events: "Queue[tuple[StreamEvent, int | None]]" = Queue()
    streamer.on_event(lambda event, code: events.put((event, code)))

    streamer.start("UDID-2", sink)
    try:
        first = events.get(timeout=_TIMEOUT_S)
        second = events.get(timeout=_TIMEOUT_S)
    finally:
        streamer.stop()

    assert first == (StreamEvent.DISCONNECTED, 1)
    assert second == (StreamEvent.PROCESS_EXITED, 1)


# ---------------------------------------------------------------------------
# Scenario 3: stop releases resources and suppresses events (Req 2.5)
# ---------------------------------------------------------------------------


def test_stop_terminates_joins_and_suppresses_events():
    # A process that stays alive until terminated (stdout blocks).
    process = FakeProcess([], returncode=0, block_until_terminated=True)
    streamer = LogStreamer(process_factory=_factory_for(process))
    sink: "Queue[RawLogLine]" = Queue()

    events: "Queue[tuple[StreamEvent, int | None]]" = Queue()
    streamer.on_event(lambda event, code: events.put((event, code)))

    streamer.start("UDID-3", sink)
    # Capture the reader thread so we can assert it is joined after stop.
    reader_thread = streamer._thread  # noqa: SLF001 - integration assertion
    assert reader_thread is not None and reader_thread.is_alive()
    assert streamer.is_running

    streamer.stop()

    # Subprocess was terminated and the reader thread joined (not alive).
    assert process.terminate_called or process.kill_called
    assert not reader_thread.is_alive()
    assert not streamer.is_running

    # A user-initiated stop suppresses lifecycle events.
    with pytest.raises(Empty):
        events.get(timeout=0.2)


def test_stop_is_idempotent_and_safe_when_never_started():
    # Safe on a never-started streamer.
    never_started = LogStreamer(process_factory=_factory_for(FakeProcess([])))
    never_started.stop()  # must not raise
    assert not never_started.is_running

    # Idempotent: calling stop twice is safe.
    process = FakeProcess([], returncode=0, block_until_terminated=True)
    streamer = LogStreamer(process_factory=_factory_for(process))
    streamer.start("UDID-4", Queue())
    streamer.stop()
    streamer.stop()  # second call must be a no-op, not raise
    assert not streamer.is_running
