"""Output_Display (I/O): render device lists, log lines, progress and notices.

This component is the CLI presentation layer. It renders four kinds of output
to a configurable text stream (``sys.stdout`` by default):

* the list of detected iOS devices (with an index so the user can select one),
* filtered log lines, each annotated with its local receive timestamp,
* the latest indexing progress, and
* informational notices / error messages.

Progress display semantics (Req 4.3, 4.4)
----------------------------------------
The display tracks the **most recently successfully parsed** progress. Each
call to :meth:`update_progress` stores the supplied
:class:`~spotlight_monitor.models.IndexingProgress` as the current "latest"
progress and renders it. When no new progress has been parsed the display
continues to show the previous progress together with its ``observed_at``
timestamp (via :meth:`redraw_progress`). Before any progress has been parsed
successfully, no progress is shown at all.

The most recently parsed progress is exposed as observable state through the
:attr:`last_progress` property (``None`` when nothing has been parsed yet) so
callers — and tests — can query exactly what the display considers the latest
progress. This is the invariant exercised by Property 6 ("最近进度保持").

CLI progress area (mixed output)
--------------------------------
Progress is rendered with a **leading carriage return** (``\\r``) and **no
trailing newline**, so successive progress updates refresh the current line
in place. Log lines, notices and errors are written as normal newline-
terminated lines that scroll above the progress area. This yields the
"in-place progress refresh + scrolling log" hybrid described in the design.

Design reference: ``design.md`` -> "Output_Display".
Requirements: 1.2, 1.5, 4.3, 4.4, 4.5, 6.2, 6.5.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .models import DeviceInfo, IndexingProgress, RawLogLine

__all__ = ["OutputDisplay"]


class OutputDisplay:
    """Render filtered log lines, latest progress and notices (Req 4, 6 prompts).

    All output is written to ``stream`` (defaults to ``sys.stdout``). Injecting
    a stream (e.g. :class:`io.StringIO`) lets callers and tests capture the
    rendered output deterministically.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        """Create a display that writes to ``stream`` (default ``sys.stdout``).

        The most-recently-parsed progress starts as ``None`` (nothing parsed
        yet), so no progress is displayed until the first successful parse is
        reported via :meth:`update_progress`.
        """
        self._stream: TextIO = stream if stream is not None else sys.stdout
        # The latest successfully parsed progress, or None when nothing has been
        # parsed yet. This is the observable state behind Property 6.
        self._last_progress: IndexingProgress | None = None

    # -- observable state --------------------------------------------------

    @property
    def last_progress(self) -> IndexingProgress | None:
        """The most recently parsed progress, or ``None`` if none yet (4.4).

        At any point this equals the last :class:`IndexingProgress` passed to
        :meth:`update_progress`, and is ``None`` before the first successful
        parse. This is the "currently displayed latest progress" queried by
        Property 6.
        """
        return self._last_progress

    # -- device list (1.2, 1.5) -------------------------------------------

    def show_devices(self, devices: list[DeviceInfo]) -> None:
        """Render the list of detected devices, one per line (1.2, 1.5).

        Each device is prefixed with a 0-based index so the user can select a
        target device when multiple are connected (Req 1.5). For every device
        the UDID, name (``<unknown>`` when not available) and state are shown.
        When the list is empty a single placeholder line is rendered.
        """
        if not devices:
            self._writeln("未检测到已连接的 iOS 设备。")
            return

        self._writeln("检测到的设备：")
        for index, device in enumerate(devices):
            name = device.name if device.name else "<unknown>"
            self._writeln(
                f"  [{index}] {device.udid}  {name}  ({device.state.value})"
            )

    # -- log lines (3.2, 4.5) ---------------------------------------------

    def show_log_line(self, line: RawLogLine) -> None:
        """Render a filtered log line with its local receive timestamp (4.5).

        The line is rendered as ``{received_at_isoformat}  {text}`` so every
        displayed line carries the timestamp at which it was received locally
        (Req 4.5). Rendered as a normal scrolling line above the progress area.
        """
        self._writeln(f"{line.received_at.isoformat()}  {line.text}")

    # -- progress (4.3, 4.4) ----------------------------------------------

    def update_progress(self, progress: IndexingProgress) -> None:
        """Store ``progress`` as the latest and render it in place (4.3, 4.4).

        The supplied progress becomes the new "latest successfully parsed"
        progress (observable via :attr:`last_progress`) and is rendered to the
        progress area. Because parsing only calls this on a successful parse,
        the stored value always reflects the last successful extraction.
        """
        self._last_progress = progress
        self._render_progress(progress)

    def redraw_progress(self) -> None:
        """Re-render the last parsed progress, if any (4.4).

        Used when no new progress has been parsed but the progress area should
        continue to display the most recent progress together with its
        ``observed_at`` timestamp. Does nothing when no progress has been parsed
        yet, so nothing is shown before the first successful parse.
        """
        if self._last_progress is not None:
            self._render_progress(self._last_progress)

    def _render_progress(self, progress: IndexingProgress) -> None:
        """Write the progress line in place (leading CR, no trailing newline).

        Includes the normalized percent and the ``observed_at`` timestamp so a
        held (un-refreshed) progress still shows when it was last observed
        (Req 4.4).
        """
        text = (
            f"\r索引进度: {progress.percent:.1f}% "
            f"(更新于 {progress.observed_at.isoformat()})"
        )
        self._stream.write(text)
        self._stream.flush()

    # -- notices and errors (1.3, 1.4, 6.2, 6.5 / 6.3) --------------------

    def show_notice(self, message: str) -> None:
        """Render an informational notice on its own line (1.3, 1.4, 6.2, 6.5)."""
        self._writeln(message)

    def show_error(self, message: str) -> None:
        """Render an error message on its own line (6.3).

        Prefixed with ``错误:`` so errors are visually distinguishable from
        ordinary notices and log lines.
        """
        self._writeln(f"错误: {message}")

    # -- internal helpers --------------------------------------------------

    def _writeln(self, text: str) -> None:
        """Write ``text`` followed by a newline to the output stream and flush."""
        self._stream.write(f"{text}\n")
        self._stream.flush()
