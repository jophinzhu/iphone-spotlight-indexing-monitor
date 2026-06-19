"""Property-based test for Log_Streamer arrival-order preservation (Task 8.2).

Uses Hypothesis to verify that the order in which :class:`LogStreamer` delivers
lines to the downstream sink queue equals the order in which they arrive (are
captured) from the underlying process stdout.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 8.

Threading note
--------------
``LogStreamer`` reads stdout in a daemon background thread. For each example we
build a fresh streamer + fake process + sink queue, start it, then drain the
queue until we have collected exactly ``len(lines)`` items (using a bounded
``Queue.get(timeout=...)`` loop with an overall safety deadline). Hypothesis'
per-example deadline is disabled because thread scheduling can occasionally
exceed the default deadline.
"""

from __future__ import annotations

import time
from queue import Empty, Queue

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.log_streamer import LogStreamer
from spotlight_monitor.models import RawLogLine

# A single log line's text. Embedded newlines ("\n", "\r") are EXCLUDED: the
# reader iterates stdout line by line and rstrips newline chars, so real lines
# never carry them. With them excluded the expected text equals the input line
# verbatim, making the order assertion unambiguous.
_line_text = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=0x2FFF,
        blacklist_characters="\n\r",
    ),
    min_size=0,
    max_size=60,
)

# Allow the empty list (0 lines) as well as longer sequences.
_lines = st.lists(_line_text, min_size=0, max_size=40)


class _FakeProcess:
    """A minimal process double that yields ``lines`` then exits naturally.

    ``stdout`` is an iterator over the lines; once exhausted the process is
    considered finished (``poll`` returns the return code, ``wait`` returns it).
    ``terminate``/``kill`` are no-ops that set a flag.
    """

    def __init__(self, lines: list[str]) -> None:
        # Re-add the newline the reader is expected to strip, mirroring how a
        # real text-mode pipe yields newline-terminated lines.
        self.stdout = iter([f"{line}\n" for line in lines])
        self._returncode: int | None = None
        self._total = len(lines)
        self._emitted = 0
        self.terminated = False

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True


# Feature: iphone-spotlight-indexing-monitor, Property 8: 到达顺序保持
@settings(max_examples=100, deadline=None)
@given(lines=_lines)
def test_property_8_arrival_order_preserved(lines: list[str]) -> None:
    """Lines delivered to the sink appear in the same order they were captured.

    A fresh :class:`LogStreamer` is started with a fake factory returning a
    :class:`_FakeProcess` that yields exactly ``lines`` then ends. We drain the
    sink queue until ``len(lines)`` items have been collected (bounded by a
    safety timeout), then assert the collected texts equal the input lines in
    the same order (Property 8 / Req 2.2).
    """
    sink: "Queue[RawLogLine]" = Queue()

    def factory(cmd: list[str]) -> _FakeProcess:
        return _FakeProcess(lines)

    streamer = LogStreamer(process_factory=factory)
    streamer.start(udid="x", sink=sink)

    collected: list[RawLogLine] = []
    deadline = time.monotonic() + 10.0  # overall safety deadline
    try:
        while len(collected) < len(lines):
            remaining = deadline - time.monotonic()
            assert remaining > 0, (
                f"timed out collecting lines: got {len(collected)} of {len(lines)}"
            )
            try:
                collected.append(sink.get(timeout=remaining))
            except Empty:  # pragma: no cover - defensive, deadline catches it
                break
    finally:
        streamer.stop()

    assert [item.text for item in collected] == lines
