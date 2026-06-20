"""Device_Connector: enumerate and identify USB-connected iOS devices (Req 1).

This is an **I/O** component: it shells out to the libimobiledevice CLI tools to
discover devices and determine their pairing / lock state.

- ``idevice_id -l`` lists the UDIDs of connected devices, one per line.
- ``ideviceinfo -u <UDID> -k DeviceName`` returns the device name and, via its
  return code / stderr, reveals whether the device is paired, awaiting a trust
  prompt, or locked behind a passcode.

Design notes
------------
The exact wording of ``ideviceinfo`` errors varies between libimobiledevice
versions and iOS releases, so state detection is driven by a small,
**heuristic, pure helper** -- :func:`classify_state` -- that maps a
``(returncode, stderr)`` pair to a :class:`~spotlight_monitor.models.DeviceState`.
Keeping the classification pure means task 7.2's integration tests can exercise
it directly and can mock the subprocess layer without touching real hardware.

Subprocess invocation is **injectable**: the constructor accepts an optional
``runner`` callable ``(cmd, timeout) -> CompletedProcess``-like object,
defaulting to :func:`subprocess.run`. Tests can pass a fake runner to simulate
any device topology, error text, timeout or missing executable.

Robustness: :meth:`enumerate_devices` never crashes on a missing executable
(``FileNotFoundError``) or a slow tool (``subprocess.TimeoutExpired``); it
returns an empty list instead. (Verifying that the tools are installed is the
job of task 11.1's dependency check, not this module.)

Design reference: ``design.md`` -> "Device_Connector".

Requirements: 1.1, 1.2, 1.4, 6.5.
"""

from __future__ import annotations

import subprocess
from typing import Protocol

from ._paths import get_bundled_env, get_executable
from .models import DeviceInfo, DeviceState

__all__ = ["DeviceConnector", "classify_state", "CompletedProcessLike", "Runner"]


class CompletedProcessLike(Protocol):
    """The minimal shape :class:`DeviceConnector` needs from a process result.

    Matches :class:`subprocess.CompletedProcess` when invoked with
    ``capture_output=True, text=True`` (i.e. ``stdout`` / ``stderr`` are ``str``).
    """

    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    """A callable that runs a command and returns a completed-process result.

    Implementations must run ``cmd`` to completion within ``timeout`` seconds
    and either return a :class:`CompletedProcessLike` or raise
    :class:`subprocess.TimeoutExpired` / :class:`FileNotFoundError`.
    """

    def __call__(
        self, cmd: list[str], timeout: float
    ) -> CompletedProcessLike: ...


def _default_runner(cmd: list[str], timeout: float) -> CompletedProcessLike:
    """Default :class:`Runner`: run ``cmd`` via :func:`subprocess.run`.

    Captures stdout/stderr as text and enforces ``timeout`` seconds. On Windows
    the executables are invoked directly (no ``cmd /c`` wrapper).
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=get_bundled_env(),
    )


# ----------------------------------------------------------------------------
# Pure state classification (testable in isolation)
# ----------------------------------------------------------------------------

# Substrings (matched case-insensitively) that indicate the device is connected
# but locked behind a passcode -- ideviceinfo cannot read values until the user
# unlocks the screen (Req 6.5). Checked BEFORE the pairing markers because a
# locked-device error is the more specific condition.
_LOCKED_MARKERS: tuple[str, ...] = (
    "passcode",                      # "Please enter the passcode ..."
    "passcode protected",            # "device is passcode protected"
    "locked",                        # generic "LOCKED" / "device is locked"
    "enter the passcode",
)

# Substrings (matched case-insensitively) that indicate the device is connected
# but not yet trusted/paired with this host -- the user must tap "Trust This
# Computer" on the iPhone (Req 1.4).
_UNPAIRED_MARKERS: tuple[str, ...] = (
    "pairing",                       # "... please accept the pairing ..."
    "not paired",
    "pair the device",
    "trust",                         # "accept the trust dialog"
    "invalidhostid",                 # lockdownd error when host is unknown
    "could not connect to lockdownd",  # commonly seen when unpaired
    "please accept",
)


def classify_state(returncode: int, stderr: str) -> DeviceState:
    """Map an ``ideviceinfo`` result to a :class:`DeviceState` (heuristic, pure).

    The classification rules, in priority order:

    1. ``returncode == 0`` -> :attr:`DeviceState.CONNECTED_PAIRED`. ``ideviceinfo``
       successfully read a value, so the device is connected and paired (Req 1.2).
    2. ``stderr`` matches a *locked* marker (e.g. mentions a passcode) ->
       :attr:`DeviceState.LOCKED` (Req 6.5). Checked before pairing because it
       is the more specific signal.
    3. ``stderr`` matches a *pairing/trust* marker ->
       :attr:`DeviceState.CONNECTED_UNPAIRED` (Req 1.4).
    4. Otherwise (non-zero exit, unrecognized error) -> default to
       :attr:`DeviceState.CONNECTED_UNPAIRED`. The device is present (it was
       listed by ``idevice_id -l``) but we cannot read it, and prompting the
       user to trust the computer is the safest, most actionable default.

    The exact error strings differ across libimobiledevice / iOS versions, so
    matching is done on lowercase substrings rather than exact equality.
    """
    if returncode == 0:
        return DeviceState.CONNECTED_PAIRED

    haystack = (stderr or "").lower()

    if any(marker in haystack for marker in _LOCKED_MARKERS):
        return DeviceState.LOCKED

    if any(marker in haystack for marker in _UNPAIRED_MARKERS):
        return DeviceState.CONNECTED_UNPAIRED

    # Present but unreadable for an unknown reason: prompt to trust (Req 1.4).
    return DeviceState.CONNECTED_UNPAIRED


class DeviceConnector:
    """Enumerate and identify USB-connected iOS devices (Req 1)."""

    def __init__(
        self,
        idevice_id_path: str | None = None,
        ideviceinfo_path: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        """Create a connector.

        Args:
            idevice_id_path: Path to (or bare name of) the ``idevice_id``
                executable. Defaults to the bundled path or bare name on PATH.
            ideviceinfo_path: Path to (or bare name of) the ``ideviceinfo``
                executable. Defaults to the bundled path or bare name on PATH.
            runner: Optional injectable command runner used for every
                subprocess call. Defaults to :func:`_default_runner` (which
                wraps :func:`subprocess.run`). Tests can supply a fake to avoid
                touching real hardware.
        """
        self._idevice_id_path = idevice_id_path or get_executable("idevice_id")
        self._ideviceinfo_path = ideviceinfo_path or get_executable("ideviceinfo")
        self._runner: Runner = runner if runner is not None else _default_runner

    def enumerate_devices(self, timeout_s: float = 5.0) -> list[DeviceInfo]:
        """Enumerate all USB-connected iOS devices within ``timeout_s`` (1.1, 1.2).

        Runs ``idevice_id -l`` to obtain the connected UDIDs, then for each UDID
        resolves its name and pairing/lock state. Returns one
        :class:`DeviceInfo` per device, preserving the order reported by
        ``idevice_id``.

        This method never raises for environmental problems: if the executable
        is missing (:class:`FileNotFoundError`) or a tool exceeds ``timeout_s``
        (:class:`subprocess.TimeoutExpired`), an **empty list** is returned
        (Req 1.1). Detecting missing dependencies is task 11.1's concern.
        """
        result = self._run([self._idevice_id_path, "-l"], timeout_s)
        if result is None or result.returncode != 0:
            # Tool missing/timed out, or reported an error: no devices to show.
            return []

        udids = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        devices: list[DeviceInfo] = []
        for udid in udids:
            name = self._get_device_name(udid, timeout_s)
            state = self.get_pairing_state(udid, timeout_s)
            devices.append(DeviceInfo(udid=udid, name=name, state=state))
        return devices

    def get_pairing_state(
        self, udid: str, timeout_s: float = 5.0
    ) -> DeviceState:
        """Determine the pairing / lock state of ``udid`` (1.4, 6.5).

        Invokes ``ideviceinfo -u <udid> -k DeviceName`` and classifies the
        outcome via :func:`classify_state`. If the executable is missing or the
        call times out, the state cannot be determined; we conservatively
        report :attr:`DeviceState.CONNECTED_UNPAIRED` so the user is prompted to
        trust the computer.
        """
        result = self._run(
            [self._ideviceinfo_path, "-u", udid, "-k", "DeviceName"], timeout_s
        )
        if result is None:
            # Missing executable or timeout: state is unknown -> prompt to trust.
            return DeviceState.CONNECTED_UNPAIRED
        return classify_state(result.returncode, result.stderr)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_device_name(self, udid: str, timeout_s: float) -> str | None:
        """Return the device name for ``udid``, or ``None`` if unavailable (1.2).

        A name is only available when ``ideviceinfo`` succeeds (return code 0);
        for unpaired or locked devices (and on timeout/missing executable) the
        name is ``None`` and the device is still listed by its UDID.
        """
        result = self._run(
            [self._ideviceinfo_path, "-u", udid, "-k", "DeviceName"], timeout_s
        )
        if result is None or result.returncode != 0:
            return None
        name = (result.stdout or "").strip()
        return name or None

    def _run(
        self, cmd: list[str], timeout_s: float
    ) -> CompletedProcessLike | None:
        """Run ``cmd`` via the injected runner, swallowing environment errors.

        Returns the completed-process result, or ``None`` when the executable
        is missing (:class:`FileNotFoundError`) or the call exceeds
        ``timeout_s`` (:class:`subprocess.TimeoutExpired`). Callers decide how to
        interpret ``None`` (typically: no devices / unknown state). This keeps
        :meth:`enumerate_devices` from crashing (Req 1.1).
        """
        try:
            return self._runner(cmd, timeout_s)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
