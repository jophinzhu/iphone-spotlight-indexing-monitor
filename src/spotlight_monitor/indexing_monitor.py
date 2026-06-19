"""Indexing_Monitor: the orchestrator that ties the other components together.

The :class:`IndexingMonitor` coordinates dependency checking, device selection,
log stream capture, rule hot-reloading, error handling and diagnostic logging
(Req 1, 2, 5.6, 6).

This module is built incrementally across several tasks:

- **Task 11.1 (this file's initial scope)** implements
  :meth:`IndexingMonitor.check_dependencies` (Req 6.1, 6.2) and
  :meth:`IndexingMonitor.log_diagnostic` (Req 6.4).
- **Task 11.3** extends :meth:`IndexingMonitor.apply_rules` for rule
  hot-reloading (Req 5.6).
- **Task 11.5** implements :meth:`IndexingMonitor.run`, the main entry point and
  connection-monitoring state machine (Req 1.3, 1.4, 1.5, 2.3, 2.4, 4.5, 7.3).

Design reference: ``design.md`` -> "Indexing_Monitor（协调器）".

Requirements: 1.3, 1.4, 1.5, 2.3, 2.4, 4.5, 6.1, 6.2, 6.4, 7.3.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

from .config_manager import ConfigManager
from .device_connector import DeviceConnector
from .log_filter import LogFilter
from .log_streamer import LogStreamer
from .log_writer import LogWriter, LogWriterError
from .models import (
    AppConfig,
    DeviceInfo,
    DeviceState,
    IndexingProgress,
    RawLogLine,
    StreamEvent,
)
from .output_display import OutputDisplay
from .progress_parser import ProgressParser

__all__ = [
    "IndexingMonitor",
    "RuleSnapshot",
    "DEFAULT_REQUIRED_EXECUTABLES",
    "DEFAULT_DIAGNOSTIC_LOG",
    "DEFAULT_RECONNECT_WINDOW_S",
    "EXIT_OK",
    "EXIT_MISSING_DEPENDENCIES",
    "EXIT_NO_DEVICE",
    "EXIT_DEVICE_NOT_READY",
]

# Default reconnect window: when the stream drops we poll for the device to
# come back for up to this many seconds before giving up (Req 2.4).
DEFAULT_RECONNECT_WINDOW_S: float = 10.0

# Exit codes returned by :meth:`IndexingMonitor.run`.
EXIT_OK = 0                    # normal stop (user stop / reconnect timeout)
EXIT_MISSING_DEPENDENCIES = 1  # required executables missing (Req 6.2)
EXIT_NO_DEVICE = 2             # no device found within the wait window (Req 1.3)
EXIT_DEVICE_NOT_READY = 3      # device present but unpaired/locked (Req 1.4, 6.5)


# Sentinel placed on the line queue by the stream-event callback to signal the
# processing loop that the underlying stream ended (device unplugged / process
# exited). It is a unique object so it can never collide with a real log line.
_STREAM_ENDED = object()

# The libimobiledevice executables the tool relies on. ``idevice_id`` enumerates
# connected devices, ``ideviceinfo`` resolves name / pairing state, and
# ``idevicesyslog`` streams the live system log (design.md "技术选型").
DEFAULT_REQUIRED_EXECUTABLES: tuple[str, ...] = (
    "idevice_id",
    "ideviceinfo",
    "idevicesyslog",
)

# Default path for the local diagnostic log file (Req 6.4). Set to None to
# disable diagnostic logging by default. Users can enable it via CLI flag.
DEFAULT_DIAGNOSTIC_LOG: Path | None = None

# Per-executable fix guidance appended to the "missing dependency" message so
# the user knows exactly how to resolve it (Req 6.2).
_FIX_GUIDANCE = (
    "请安装 libimobiledevice 套件并确保其可执行文件已加入系统 PATH"
    "（Windows 可参考 imobiledevice-net 或 scoop/choco 安装包）"
)


@dataclass(frozen=True)
class RuleSnapshot:
    """An immutable snapshot of the active rule set for hot-reloading (5.6).

    A snapshot bundles the source :class:`AppConfig` together with the two
    pure-logic collaborators derived from it — a :class:`LogFilter` and a
    :class:`ProgressParser`. Bundling them keeps the filter and parser
    consistent with one another and with the config they were built from: a
    reader that grabs the current snapshot always sees a *coherent* trio, never
    a filter from one config paired with a parser from another.

    The dataclass is ``frozen=True`` so an individual snapshot can never be
    mutated in place. Hot-reload is therefore performed by building a brand-new
    snapshot and *replacing the reference* the monitor holds
    (:meth:`IndexingMonitor.apply_rules`), rather than by editing a shared
    object. The contained :class:`LogFilter` / :class:`ProgressParser` are
    themselves effectively immutable (their rules are fixed at construction), so
    the whole snapshot is safe to read concurrently from the processing thread.
    """

    config: AppConfig
    log_filter: LogFilter
    parser: ProgressParser

    @classmethod
    def from_config(cls, config: AppConfig) -> "RuleSnapshot":
        """Build a snapshot from ``config``.

        Constructs the :class:`LogFilter` (honoring ``config.case_sensitive``)
        and :class:`ProgressParser` from the config's filter and parse rules.
        """
        return cls(
            config=config,
            log_filter=LogFilter(config.filter_rules, config.case_sensitive),
            parser=ProgressParser(config.parse_rules),
        )


class IndexingMonitor:
    """Coordinator for the Spotlight indexing monitor (Req 1, 2, 5.6, 6)."""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        required_executables: tuple[str, ...] = DEFAULT_REQUIRED_EXECUTABLES,
        diagnostic_log_path: Path | str | None = DEFAULT_DIAGNOSTIC_LOG,
        which: Callable[[str], str | None] = shutil.which,
        device_connector: DeviceConnector | None = None,
        log_streamer: LogStreamer | None = None,
        output_display: OutputDisplay | None = None,
        log_writer: LogWriter | None = None,
    ) -> None:
        """Create the orchestrator.

        Args:
            config: The active application configuration (filter / parse rules).
                When ``None`` the built-in defaults are used. Defaults to
                ``None``.
            required_executables: The libimobiledevice executable names that
                must be resolvable on ``PATH`` for the tool to run. Injectable so
                tests can supply a custom set. Defaults to
                :data:`DEFAULT_REQUIRED_EXECUTABLES`.
            diagnostic_log_path: Destination for the local diagnostic log
                (Req 6.4). Injectable so tests can point it at a temp file.
                Defaults to :data:`DEFAULT_DIAGNOSTIC_LOG`.
            which: Callable used to resolve an executable name to a path (or
                ``None`` when absent). Injectable for testing; defaults to
                :func:`shutil.which`.
            device_connector: The :class:`DeviceConnector` used to enumerate /
                re-detect devices (Req 1, 2.4). Injectable for testing; a real
                one is constructed lazily when omitted.
            log_streamer: The :class:`LogStreamer` used to capture the live
                system log (Req 2). Injectable for testing; a real one is
                constructed lazily when omitted.
            output_display: The :class:`OutputDisplay` used to render devices,
                lines, progress and notices (Req 1, 4, 6 prompts). Injectable
                for testing; a real one is constructed lazily when omitted.
            log_writer: An **optional, already-opened** :class:`LogWriter`. When
                supplied, every filtered line is also persisted to disk (Req 7).
                The caller owns the writer's file lifecycle (``open`` / ``close``);
                ``run`` only calls :meth:`LogWriter.write`. Defaults to ``None``
                (no disk persistence).
        """
        self._config = config
        self._required_executables = tuple(required_executables)
        self._diagnostic_log_path: Path | None = Path(diagnostic_log_path) if diagnostic_log_path else None
        self._which = which

        # Injected collaborators (constructed lazily in ``run`` when omitted so
        # ``__init__`` stays side-effect free and cheap to instantiate).
        self._device_connector = device_connector
        self._log_streamer = log_streamer
        self._output_display = output_display
        self._log_writer = log_writer

        # Build the initial immutable rule snapshot (Req 5.6). When no config is
        # supplied we fall back to the built-in defaults so the filter/parser are
        # always usable. ``_snapshot`` is the single reference the processing
        # thread reads; it is replaced atomically by ``apply_rules``.
        initial_config = config if config is not None else ConfigManager.default_config()
        self._snapshot: RuleSnapshot = RuleSnapshot.from_config(initial_config)

    # ------------------------------------------------------------------
    # Task 11.1: dependency checking & diagnostic logging
    # ------------------------------------------------------------------

    def check_dependencies(self) -> list[str]:
        """Verify the required low-level components are available (6.1, 6.2).

        Resolves each required libimobiledevice executable via the injected
        ``which`` callable (:func:`shutil.which` by default). For every
        executable that cannot be found on ``PATH`` a human-readable error
        string is produced that names the missing item and includes fix
        guidance (Req 6.2).

        Returns:
            A list of error messages, one per missing dependency. An **empty
            list** means every dependency is present and startup may proceed
            (Req 6.1). A non-empty list means startup should be aborted and the
            messages shown to the user.

        Notes:
            On Windows, live syslog capture also relies on the Apple Mobile
            Device USB driver (installed with iTunes / the Apple Devices app).
            That driver cannot be verified reliably from Python, so this check
            focuses on the executables. When any executable is missing we append
            a single best-effort note reminding the user to also ensure the USB
            driver is installed; this keeps the check pragmatic without
            producing false negatives when the driver is actually present.
        """
        errors: list[str] = []
        for exe in self._required_executables:
            if self._which(exe) is None:
                errors.append(f"缺少可执行文件 {exe}：{_FIX_GUIDANCE}")

        if errors:
            # Best-effort USB driver guidance: we cannot positively detect the
            # Apple Mobile Device USB driver, so only surface this hint when
            # something is already missing (avoids false negatives).
            errors.append(
                "另请确认已安装 Apple Mobile Device USB 驱动"
                "（随 iTunes / Apple Devices 安装），否则无法经 USB 读取设备日志"
            )

        return errors

    def log_diagnostic(self, detail: str, category: str = "ERROR") -> None:
        """Append an error detail to the local diagnostic log file (6.4).

        Writes a single tab-separated record of the form
        ``<ISO-8601 timestamp>\\t<category>\\t<detail>`` to the configured
        diagnostic log file, creating the parent directory if needed.

        The call is **best-effort and never raises**: if the file cannot be
        opened or written (e.g. permission denied, read-only location), the
        failure is swallowed so diagnostic logging never becomes the cause of a
        crash. The method is callable as ``log_diagnostic(detail)``; an optional
        ``category`` may be supplied to classify the record.

        When no diagnostic log path is configured (the default), this method is
        a no-op.

        Args:
            detail: The error detail / message to record.
            category: A short category label for the record. Defaults to
                ``"ERROR"``.
        """
        if self._diagnostic_log_path is None:
            return

        timestamp = datetime.now().isoformat()
        # Normalize whitespace that would corrupt the tab-separated, one-record-
        # per-line format.
        safe_category = category.replace("\t", " ").replace("\n", " ")
        safe_detail = detail.replace("\t", " ").replace("\n", " ")
        record = f"{timestamp}\t{safe_category}\t{safe_detail}\n"

        try:
            parent = self._diagnostic_log_path.parent
            if parent and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
            with self._diagnostic_log_path.open("a", encoding="utf-8") as fh:
                fh.write(record)
        except OSError:
            # Diagnostic logging must never crash the tool (Req 6 principle:
            # "尽量不中断采集"). Swallow any filesystem error.
            return

    # ------------------------------------------------------------------
    # Task 11.3: rule hot-reload (immutable snapshot, atomic replacement)
    # ------------------------------------------------------------------

    def apply_rules(self, config: AppConfig) -> None:
        """Atomically swap the active rule snapshot for hot-reload (5.6).

        Builds a fresh, immutable :class:`RuleSnapshot` from ``config`` (a new
        :class:`LogFilter` and :class:`ProgressParser`) and then replaces the
        monitor's snapshot reference in a **single attribute assignment**.

        **Atomicity mechanism.** In CPython, rebinding an instance attribute
        (``self._snapshot = new_snapshot``) is a single bytecode store that
        executes atomically with respect to the GIL. A concurrent reader on the
        processing thread that reads ``self._snapshot`` therefore observes
        *either* the complete old snapshot *or* the complete new one — never a
        half-updated, torn state. Because the snapshot is frozen and the new
        filter/parser are fully constructed *before* the assignment, there is no
        window in which a reader could see an inconsistent filter/parser pair.

        The new snapshot only affects lines processed *after* the swap; the
        underlying log stream is never restarted (Req 5.6). Lines already being
        processed against the old snapshot finish with the old rules.

        Args:
            config: The new application configuration to apply going forward.
        """
        new_snapshot = RuleSnapshot.from_config(config)
        # Single atomic rebind — see the docstring for why this is safe.
        self._snapshot = new_snapshot
        # Keep the recorded config in sync with the active snapshot.
        self._config = config

    @property
    def current_snapshot(self) -> RuleSnapshot:
        """Return the currently active immutable rule snapshot (5.6).

        The processing thread (task 11.5) and tests should read this once per
        line so that an ``apply_rules`` call mid-stream takes effect on the
        *next* line without restarting the stream.
        """
        return self._snapshot

    @property
    def current_filter(self) -> LogFilter:
        """The :class:`LogFilter` from the active snapshot (convenience accessor)."""
        return self._snapshot.log_filter

    @property
    def current_parser(self) -> ProgressParser:
        """The :class:`ProgressParser` from the active snapshot (convenience accessor)."""
        return self._snapshot.parser

    def process_line(self, line: RawLogLine) -> IndexingProgress | None:
        """Filter then parse ``line`` using the *current* snapshot (5.6).

        Reads the active snapshot reference once and applies its filter and
        parser to ``line``. Returns the extracted :class:`IndexingProgress` when
        the line both matches the filter and yields a parseable progress value;
        returns ``None`` when the line is filtered out or no progress can be
        parsed (parsing is non-fatal).

        This mirrors the per-line processing the main loop performs, and gives
        the hot-reload property test a single entry point whose behavior depends
        only on the snapshot active at call time.
        """
        snapshot = self._snapshot  # read the reference once (atomic)
        if not snapshot.log_filter.matches(line.text):
            return None
        return snapshot.parser.parse(line)

    # ------------------------------------------------------------------
    # Task 11.5: main entry point & connection-monitoring state machine
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        reconnect_window_s: float = DEFAULT_RECONNECT_WINDOW_S,
        poll_interval_s: float = 0.5,
        device_wait_s: float = 5.0,
        device_selector: Callable[[list[DeviceInfo]], DeviceInfo] | None = None,
        queue: "Queue[RawLogLine] | None" = None,
    ) -> int:
        """Run the monitor: the main entry point and state machine (Req 1, 2, 4.5, 7.3).

        Implements the session state machine from ``design.md``::

            检查依赖 → 枚举设备 → 等待设备/选择设备/流式采集 → 等待重连 → 已停止

        Control flow:

        1. **检查依赖** — :meth:`check_dependencies`. On any missing dependency
           the items are shown via :meth:`OutputDisplay.show_error`, recorded to
           the diagnostic log, and :data:`EXIT_MISSING_DEPENDENCIES` is returned
           (startup aborted, Req 6.2).
        2. **枚举设备 / 等待设备** — enumerate via the :class:`DeviceConnector`.
           When none are found we prompt the user to connect + unlock (Req 1.3)
           and poll for up to ``device_wait_s`` seconds; if still none we return
           :data:`EXIT_NO_DEVICE`. The wait is bounded and honors ``stop_event``
           so it never blocks forever (testability).
        3. **选择设备** — when several devices are present the target is chosen
           by ``device_selector`` (default: the first device), after rendering
           the list (Req 1.5). An unpaired device prompts "Trust This Computer"
           (Req 1.4) and a locked device prompts to unlock (Req 6.5); both return
           :data:`EXIT_DEVICE_NOT_READY` since the stream cannot start.
        4. **流式采集 + 处理循环** — start the :class:`LogStreamer`, then pull
           :class:`RawLogLine` items off the queue and run each through the
           *current* rule snapshot: matching lines are shown (and optionally
           written to disk), and a parsed :class:`IndexingProgress` updates the
           display. The snapshot is read per line so :meth:`apply_rules` hot-
           reloads take effect without restarting the stream (Req 5.6).
        5. **等待重连** — when the stream ends (device unplugged / process exit)
           a sentinel wakes the loop; we poll the connector for up to
           ``reconnect_window_s`` seconds and, if the device returns, restart the
           stream automatically (Req 2.3, 2.4). On timeout or a set ``stop_event``
           the loop ends (已停止).

        ``received_at`` is never reconstructed: the very :class:`RawLogLine`
        captured by the streamer is the object passed to the filter, parser,
        display and writer, so its ``received_at`` flows through unchanged
        (Req 4.5, 7.3).

        Args:
            stop_event: Cooperative stop signal (Req 2.5). When set the loop and
                all bounded waits exit promptly. A fresh, never-set event is
                created when omitted (the loop then runs until the stream ends
                with no reconnect, or ``KeyboardInterrupt``).
            reconnect_window_s: Seconds to wait for the device to return after a
                disconnect (Req 2.4). Defaults to :data:`DEFAULT_RECONNECT_WINDOW_S`.
            poll_interval_s: Queue/poll granularity in seconds. Smaller values
                make the loop more responsive to ``stop_event``.
            device_wait_s: Seconds to wait for a device to appear during initial
                enumeration before giving up (Req 1.3).
            device_selector: Chooses the target among multiple devices. Defaults
                to picking the first.
            queue: The line queue the streamer writes to. A fresh
                :class:`queue.Queue` is created when omitted (injectable for
                tests).

        Returns:
            One of the ``EXIT_*`` codes (``0`` on a normal stop).
        """
        stop = stop_event if stop_event is not None else threading.Event()
        connector = self._device_connector or DeviceConnector()
        streamer = self._log_streamer or LogStreamer()
        display = self._output_display or OutputDisplay()
        selector = device_selector or (lambda devices: devices[0])
        line_queue: "Queue[RawLogLine]" = queue if queue is not None else Queue()

        # 1) Dependency check (Req 6.1, 6.2).
        missing = self.check_dependencies()
        if missing:
            for item in missing:
                display.show_error(item)
                self.log_diagnostic(item, category="DEPENDENCY")
            return EXIT_MISSING_DEPENDENCIES

        # 2) Enumerate / wait for a device (Req 1.1, 1.3).
        devices = self._wait_for_devices(
            connector, display, device_wait_s, poll_interval_s, stop
        )
        if not devices:
            display.show_notice("未检测到已连接的 iOS 设备，请连接 iPhone 并解锁屏幕。")
            self.log_diagnostic("枚举设备：未检测到任何设备", category="NO_DEVICE")
            return EXIT_NO_DEVICE

        # 3) Select the target device (Req 1.2, 1.5) and verify it is usable.
        display.show_devices(devices)
        target = selector(devices)

        if target.state is DeviceState.LOCKED:
            display.show_notice(f"设备 {target.udid} 已锁屏，请解锁后重试。")
            self.log_diagnostic(f"设备 {target.udid} 锁屏", category="LOCKED")
            return EXIT_DEVICE_NOT_READY
        if target.state is DeviceState.CONNECTED_UNPAIRED:
            display.show_notice(
                f"设备 {target.udid} 尚未配对，请在 iPhone 上点击“信任此电脑”。"
            )
            self.log_diagnostic(f"设备 {target.udid} 未配对", category="UNPAIRED")
            return EXIT_DEVICE_NOT_READY

        # 4 & 5) Stream and process, with connection monitoring (Req 2, 4.5, 7.3).
        return self._stream_and_process(
            connector=connector,
            streamer=streamer,
            display=display,
            udid=target.udid,
            line_queue=line_queue,
            stop=stop,
            reconnect_window_s=reconnect_window_s,
            poll_interval_s=poll_interval_s,
        )

    # ------------------------------------------------------------------
    # run() helpers
    # ------------------------------------------------------------------

    def _wait_for_devices(
        self,
        connector: DeviceConnector,
        display: OutputDisplay,
        device_wait_s: float,
        poll_interval_s: float,
        stop: threading.Event,
    ) -> list[DeviceInfo]:
        """Enumerate devices, waiting up to ``device_wait_s`` for one (Req 1.1, 1.3).

        Returns as soon as at least one device is found. When none are present
        the user is prompted once to connect + unlock (Req 1.3) and we keep
        polling until a device appears, the wait elapses, or ``stop`` is set.
        The wait is bounded so the method never blocks indefinitely.
        """
        devices = connector.enumerate_devices()
        if devices:
            return devices

        # No device yet: prompt and poll within the bounded window.
        display.show_notice("等待设备接入……请连接 iPhone 并解锁屏幕。")
        deadline = time.monotonic() + device_wait_s
        while not stop.is_set() and time.monotonic() < deadline:
            time.sleep(min(poll_interval_s, max(0.0, deadline - time.monotonic())))
            devices = connector.enumerate_devices()
            if devices:
                return devices
        return devices

    def _stream_and_process(
        self,
        *,
        connector: DeviceConnector,
        streamer: LogStreamer,
        display: OutputDisplay,
        udid: str,
        line_queue: "Queue[RawLogLine]",
        stop: threading.Event,
        reconnect_window_s: float,
        poll_interval_s: float,
    ) -> int:
        """Start the stream and run the processing + reconnect loop (Req 2, 4.5, 7.3)."""

        def on_event(event: StreamEvent, code: int | None) -> None:
            # Runs on the streamer's reader thread when the process exits on its
            # own (device unplugged). DISCONNECTED announces the loss (Req 2.3);
            # PROCESS_EXITED carries the return code (Req 6.3) and wakes the
            # processing loop via the sentinel so it can attempt a reconnect.
            if event is StreamEvent.PROCESS_EXITED:
                if code:
                    self.log_diagnostic(
                        f"日志采集进程退出，退出码={code}", category="PROCESS_EXITED"
                    )
                line_queue.put(_STREAM_ENDED)  # type: ignore[arg-type]

        streamer.on_event(on_event)

        try:
            streamer.start(udid, line_queue)
        except Exception as exc:  # pragma: no cover - defensive
            display.show_error(f"无法启动日志流: {exc}")
            self.log_diagnostic(f"启动日志流失败: {exc}", category="STREAM_START")
            return EXIT_OK

        write_enabled = self._log_writer is not None
        try:
            while not stop.is_set():
                try:
                    item = line_queue.get(timeout=poll_interval_s)
                except Empty:
                    continue

                if item is _STREAM_ENDED:
                    # Connection lost: enter the "waiting for reconnect" state.
                    display.show_notice("连接已断开，正在尝试重新连接……")
                    if self._await_reconnect(
                        connector, udid, reconnect_window_s, poll_interval_s, stop
                    ):
                        # Device returned within the window: restart the stream.
                        streamer.stop()  # reset streamer state after natural exit
                        try:
                            streamer.start(udid, line_queue)
                        except Exception as exc:  # pragma: no cover - defensive
                            display.show_error(f"重连后无法恢复日志流: {exc}")
                            self.log_diagnostic(
                                f"重连恢复失败: {exc}", category="RECONNECT"
                            )
                            break
                        display.show_notice("设备已重新连接，已恢复日志流。")
                        continue
                    # Timed out (or stop requested): end the session.
                    display.show_notice("重连超时，停止监控。")
                    break

                # A real log line: process it against the CURRENT snapshot so
                # hot-reloaded rules take effect on the next line (Req 5.6).
                write_enabled = self._handle_line(item, display, write_enabled)
        except KeyboardInterrupt:  # user stop via Ctrl+C (Req 2.5)
            pass
        finally:
            streamer.stop()

        return EXIT_OK

    def _handle_line(
        self, line: RawLogLine, display: OutputDisplay, write_enabled: bool
    ) -> bool:
        """Filter/parse/display/persist one line; preserve ``received_at`` (4.5, 7.3).

        Reads the active snapshot once, then — for a matching line — shows it,
        optionally writes it to disk, and updates progress when a value parses.
        The exact :class:`RawLogLine` is passed to every stage, so its
        ``received_at`` is never reconstructed (Req 4.5, 7.3). Per the design,
        parse failures are non-fatal and any unexpected error is logged without
        crashing the loop.

        Returns the (possibly updated) ``write_enabled`` flag: a disk-write
        failure disables further writing (Req 7.4) while leaving capture running.
        """
        try:
            snapshot = self._snapshot  # read the reference once (atomic)
            if not snapshot.log_filter.matches(line.text):
                return write_enabled

            # Optionally persist the line object (received_at intact, 7.3).
            if write_enabled and self._log_writer is not None:
                try:
                    self._log_writer.write(line)
                except LogWriterError as exc:
                    display.show_error(f"日志写入失败，已停止保存: {exc}")
                    self.log_diagnostic(f"日志写入失败: {exc}", category="WRITE")
                    write_enabled = False  # stop saving but keep capturing (7.4)

            progress = snapshot.parser.parse(line)
            if progress is not None:
                # Show only the progress summary, not the raw log line.
                display.update_progress(progress)
            else:
                # No progress extracted: show the raw line.
                display.show_log_line(line)
        except Exception as exc:  # pragma: no cover - defensive, non-fatal
            self.log_diagnostic(
                f"处理日志行时发生异常: {exc}", category="PIPELINE"
            )
        return write_enabled

    def _await_reconnect(
        self,
        connector: DeviceConnector,
        udid: str,
        window_s: float,
        poll_interval_s: float,
        stop: threading.Event,
    ) -> bool:
        """Poll for the device to return within ``window_s`` seconds (Req 2.4).

        Returns ``True`` as soon as the same ``udid`` reappears in the
        :attr:`DeviceState.CONNECTED_PAIRED` state. Returns ``False`` if the
        window elapses or ``stop`` is set first. The poll is bounded so it never
        blocks indefinitely (testability).
        """
        deadline = time.monotonic() + window_s
        while not stop.is_set() and time.monotonic() < deadline:
            for device in connector.enumerate_devices():
                if device.udid == udid and device.state is DeviceState.CONNECTED_PAIRED:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval_s, remaining))
        return False
