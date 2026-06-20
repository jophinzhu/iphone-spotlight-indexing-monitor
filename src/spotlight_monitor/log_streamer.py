"""Live system-log streaming for the Spotlight indexing monitor (Req 2, 6.3).

This module wraps the libimobiledevice ``idevicesyslog`` executable, reading its
standard output line by line in a dedicated background thread. Each line is
stamped with the local receive time and pushed onto a thread-safe queue in
arrival order (Req 2.1, 2.2). Subprocess lifecycle (start / stop) and exit
detection (Req 2.3, 2.5, 6.3) are managed here.

Design reference: ``design.md`` -> "Log_Streamer".

Threading model
---------------
``start`` spawns a daemon reader thread that blocks on the subprocess stdout.
For every line read it constructs a :class:`RawLogLine` (with ``received_at``
set at read time) and ``put``\\s it on the caller-supplied queue. Because a
single reader thread performs every ``put`` in the same order it reads lines,
arrival order is preserved end to end (Property 8 / Req 2.2).

Exit-event convention
----------------------
When the subprocess ends on its own (EOF on stdout, e.g. the device was
unplugged) the reader emits, in this order, via the registered callback:

1. :attr:`StreamEvent.DISCONNECTED` - signals the connection was lost (Req 2.3).
2. :attr:`StreamEvent.PROCESS_EXITED` - always emitted on a non-user exit and
   carries the subprocess return code (Req 6.3); the code is non-zero when the
   device was unplugged.

When :meth:`stop` is called (user-initiated stop, Req 2.5) the subprocess is
terminated deliberately, so **no** ``DISCONNECTED`` / ``PROCESS_EXITED`` events
are emitted - the caller already knows it stopped the stream.

Process injection
-----------------
The subprocess is created through an injectable ``process_factory`` callable
(defaulting to :func:`subprocess.Popen`). This keeps the I/O boundary mockable
for tests: a fake process only needs an iterable ``stdout`` plus ``wait``,
``poll``, ``terminate`` and ``kill`` methods.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from queue import Queue
from typing import Callable, Protocol, runtime_checkable

from ._paths import get_bundled_env, get_executable
from .models import RawLogLine, StreamEvent

__all__ = ["ProcessLike", "ProcessFactory", "LogStreamer"]


@runtime_checkable
class ProcessLike(Protocol):
    """Minimal subprocess interface required by :class:`LogStreamer`.

    :class:`subprocess.Popen` satisfies this protocol. Test doubles need only
    provide an iterable ``stdout`` and the lifecycle methods below.
    """

    stdout: object  # an iterable of str lines (or None)

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = ...) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


# A factory takes the fully-built command (argv list) and returns a process.
ProcessFactory = Callable[[list[str]], ProcessLike]

EventCallback = Callable[[StreamEvent, "int | None"], None]


def _default_process_factory(cmd: list[str]) -> ProcessLike:
    """Spawn ``idevicesyslog`` with line-buffered text stdout.

    stderr is discarded so it cannot intermix with log lines or block the
    pipe; only stdout carries the device system log.
    """
    return subprocess.Popen(  # type: ignore[return-value]
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env=get_bundled_env(),
    )


class LogStreamer:
    """Stream an iOS device's live system log via ``idevicesyslog`` (Req 2)."""

    def __init__(
        self,
        executable: str | None = None,
        process_factory: ProcessFactory | None = None,
        *,
        terminate_timeout_s: float = 5.0,
        join_timeout_s: float = 5.0,
    ) -> None:
        """Create a streamer.

        Args:
            executable: Path/name of the ``idevicesyslog`` binary. Defaults to
                the bundled path or bare name resolved via PATH.
            process_factory: Callable building a :class:`ProcessLike` from an
                argv list. Defaults to :func:`subprocess.Popen`. Injectable for
                testing.
            terminate_timeout_s: How long :meth:`stop` waits after
                ``terminate()`` before escalating to ``kill()``.
            join_timeout_s: How long :meth:`stop` waits for the reader thread to
                finish.
        """
        self._executable = executable or get_executable("idevicesyslog")
        self._process_factory: ProcessFactory = (
            process_factory if process_factory is not None else _default_process_factory
        )
        self._terminate_timeout_s = terminate_timeout_s
        self._join_timeout_s = join_timeout_s

        self._lock = threading.Lock()
        self._process: ProcessLike | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._running = False
        self._callback: EventCallback | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether a stream is currently active."""
        with self._lock:
            return self._running

    def on_event(self, callback: EventCallback) -> None:
        """Register the stream-event callback (Req 2.3, 6.3).

        The callback receives ``(StreamEvent, exit_code | None)``. It is invoked
        for lifecycle events on a non-user subprocess exit (see module docstring
        for the exact events and ordering).
        """
        self._callback = callback

    def start(self, udid: str, sink: "Queue[RawLogLine]") -> None:
        """Start streaming the device log into ``sink`` (Req 2.1, 2.2).

        Spawns ``idevicesyslog -u <udid>`` and a daemon reader thread that
        pushes each timestamped line onto ``sink`` in arrival order.

        Raises:
            RuntimeError: if a stream is already running.
        """
        with self._lock:
            if self._running:
                raise RuntimeError("LogStreamer is already started")

            self._stopping = threading.Event()
            cmd = [self._executable, "-u", udid]
            process = self._process_factory(cmd)
            self._process = process
            thread = threading.Thread(
                target=self._reader_loop,
                args=(process, sink),
                name="log-streamer-reader",
                daemon=True,
            )
            self._thread = thread
            self._running = True

        thread.start()

    def stop(self) -> None:
        """Terminate the subprocess and release resources (Req 2.5).

        Idempotent and safe to call when never started. Because the stop is
        user-initiated, no ``DISCONNECTED`` / ``PROCESS_EXITED`` events are
        emitted.
        """
        with self._lock:
            process = self._process
            thread = self._thread
            if process is None and thread is None:
                # Never started, or already stopped: nothing to do.
                self._running = False
                return
            self._stopping.set()

        # Terminate the subprocess outside the lock so the reader thread (which
        # may invoke callbacks) can never deadlock against us.
        if process is not None:
            self._terminate_process(process)

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._join_timeout_s)

        with self._lock:
            self._process = None
            self._thread = None
            self._running = False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reader_loop(self, process: ProcessLike, sink: "Queue[RawLogLine]") -> None:
        """Blockingly read stdout line by line, timestamp and enqueue each line.

        Runs in the reader thread. On exit, reaps the return code and emits
        lifecycle events unless the exit was caused by :meth:`stop`.
        """
        try:
            stdout = getattr(process, "stdout", None)
            if stdout is not None:
                for raw in stdout:
                    # Stamp with the local receive time the moment the line is
                    # read; this value is immutable downstream (Req 4.5, 7.3).
                    line = RawLogLine(
                        text=raw.rstrip("\r\n"),
                        received_at=datetime.now(),
                    )
                    sink.put(line)
        finally:
            returncode = self._reap(process)
            stopping = self._stopping.is_set()
            with self._lock:
                self._running = False
            if not stopping:
                # Natural exit (e.g. device unplugged): announce the disconnect
                # and always report the exit code (Req 2.3, 6.3).
                self._emit(StreamEvent.DISCONNECTED, returncode)
                self._emit(StreamEvent.PROCESS_EXITED, returncode)

    def _emit(self, event: StreamEvent, code: int | None) -> None:
        callback = self._callback
        if callback is not None:
            callback(event, code)

    @staticmethod
    def _reap(process: ProcessLike) -> int | None:
        """Best-effort retrieval of the subprocess return code."""
        try:
            return process.wait()
        except Exception:
            try:
                return process.poll()
            except Exception:
                return getattr(process, "returncode", None)

    def _terminate_process(self, process: ProcessLike) -> None:
        """Terminate, then kill if it does not exit in time."""
        try:
            if process.poll() is not None:
                return  # already exited
        except Exception:
            # poll unsupported on the double; fall through to terminate.
            pass

        try:
            process.terminate()
        except Exception:
            pass

        try:
            process.wait(timeout=self._terminate_timeout_s)
            return
        except Exception:
            # Timed out or wait unsupported: escalate to kill.
            pass

        try:
            process.kill()
        except Exception:
            pass

        try:
            process.wait(timeout=self._terminate_timeout_s)
        except Exception:
            pass
