"""Integration tests for Device_Connector enumeration and timeout (Task 7.2).

Example-based (plain pytest) integration tests that exercise
:class:`~spotlight_monitor.device_connector.DeviceConnector` end-to-end through
its injectable ``runner`` seam -- i.e. with a **mock subprocess** rather than
real hardware or the real libimobiledevice executables.

A fake runner dispatches on the command:

- ``["idevice_id", "-l"]``           -> a CompletedProcess listing UDIDs.
- ``["ideviceinfo", "-u", <udid>, "-k", "DeviceName"]`` -> the name/state for
  that UDID, as dictated by the scenario.

Three representative scenarios are covered:

1. Enumeration success: two connected, paired devices (Req 1.1, 1.2).
2. Mixed states: paired + unpaired + locked recognition.
3. Timeout / missing executable: ``enumerate_devices`` returns ``[]`` and does
   not crash (Req 1.1).

Design reference: ``design.md`` -> "Testing Strategy" -> "集成测试" and
"Device_Connector".

Requirements: 1.1
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from spotlight_monitor.device_connector import DeviceConnector
from spotlight_monitor.models import DeviceState


@dataclass
class FakeCompletedProcess:
    """Minimal stand-in for :class:`subprocess.CompletedProcess` (text mode)."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Call:
    """A single recorded invocation of the fake runner."""

    cmd: list[str]
    timeout: float


class FakeRunner:
    """A configurable fake :class:`~spotlight_monitor.device_connector.Runner`.

    Dispatches on the command:

    - ``idevice_id -l`` returns ``list_result`` (or raises ``list_exc``).
    - ``ideviceinfo -u <udid> -k DeviceName`` returns the result registered for
      that UDID in ``info_results`` (or raises the registered exception).

    Every call is recorded in :attr:`calls` so tests can assert on the exact
    commands issued and the propagated ``timeout``.
    """

    def __init__(
        self,
        list_result: FakeCompletedProcess | None = None,
        info_results: dict[str, object] | None = None,
        list_exc: BaseException | None = None,
    ) -> None:
        self._list_result = list_result
        self._info_results = info_results or {}
        self._list_exc = list_exc
        self.calls: list[_Call] = []

    def __call__(self, cmd: list[str], timeout: float) -> FakeCompletedProcess:
        self.calls.append(_Call(cmd=list(cmd), timeout=timeout))

        # `idevice_id -l` -> list of UDIDs.
        if cmd[0] == "idevice_id":
            assert cmd[1:] == ["-l"]
            if self._list_exc is not None:
                raise self._list_exc
            assert self._list_result is not None
            return self._list_result

        # `ideviceinfo -u <udid> -k DeviceName` -> per-UDID name/state.
        if cmd[0] == "ideviceinfo":
            assert cmd[1] == "-u"
            udid = cmd[2]
            assert cmd[3:] == ["-k", "DeviceName"]
            outcome = self._info_results[udid]
            if isinstance(outcome, BaseException):
                raise outcome
            assert isinstance(outcome, FakeCompletedProcess)
            return outcome

        raise AssertionError(f"unexpected command: {cmd!r}")


# ---------------------------------------------------------------------------
# Scenario 1: enumeration success -- two connected, paired devices (1.1, 1.2)
# ---------------------------------------------------------------------------

def test_enumerate_two_paired_devices_success() -> None:
    """Two connected, paired devices are enumerated with names (Req 1.1, 1.2)."""
    runner = FakeRunner(
        list_result=FakeCompletedProcess(returncode=0, stdout="UDID0001\nUDID0002\n"),
        info_results={
            "UDID0001": FakeCompletedProcess(returncode=0, stdout="Alice's iPhone\n"),
            "UDID0002": FakeCompletedProcess(returncode=0, stdout="Bob's iPad\n"),
        },
    )
    connector = DeviceConnector(runner=runner)

    devices = connector.enumerate_devices(timeout_s=5.0)

    assert len(devices) == 2
    assert devices[0].udid == "UDID0001"
    assert devices[0].name == "Alice's iPhone"
    assert devices[0].state is DeviceState.CONNECTED_PAIRED
    assert devices[1].udid == "UDID0002"
    assert devices[1].name == "Bob's iPad"
    assert devices[1].state is DeviceState.CONNECTED_PAIRED

    # The first issued command is `idevice_id -l`, with the timeout propagated.
    assert runner.calls[0].cmd == ["idevice_id", "-l"]
    assert runner.calls[0].timeout == 5.0
    # Per-UDID lookups use the documented ideviceinfo command, same timeout.
    assert ["ideviceinfo", "-u", "UDID0001", "-k", "DeviceName"] in (
        c.cmd for c in runner.calls
    )
    assert all(c.timeout == 5.0 for c in runner.calls)


# ---------------------------------------------------------------------------
# Scenario 2: mixed states -- paired, unpaired and locked recognition
# ---------------------------------------------------------------------------

def test_enumerate_mixed_states_recognizes_paired_unpaired_locked() -> None:
    """Paired, unpaired (trust) and locked (passcode) devices are classified."""
    runner = FakeRunner(
        list_result=FakeCompletedProcess(
            returncode=0, stdout="PAIRED01\nUNPAIRED1\nLOCKED001\n"
        ),
        info_results={
            # Paired: success with a name.
            "PAIRED01": FakeCompletedProcess(returncode=0, stdout="My iPhone\n"),
            # Unpaired: non-zero with a pairing/trust hint -> CONNECTED_UNPAIRED.
            "UNPAIRED1": FakeCompletedProcess(
                returncode=255,
                stderr="ERROR: Could not connect to lockdownd. Please accept the trust dialog.",
            ),
            # Locked: non-zero mentioning the passcode -> LOCKED.
            "LOCKED001": FakeCompletedProcess(
                returncode=255,
                stderr="ERROR: Device is passcode protected, please enter the passcode.",
            ),
        },
    )
    connector = DeviceConnector(runner=runner)

    devices = connector.enumerate_devices(timeout_s=5.0)

    by_udid = {d.udid: d for d in devices}
    assert by_udid["PAIRED01"].state is DeviceState.CONNECTED_PAIRED
    assert by_udid["PAIRED01"].name == "My iPhone"

    assert by_udid["UNPAIRED1"].state is DeviceState.CONNECTED_UNPAIRED
    assert by_udid["UNPAIRED1"].name is None  # name only available when paired

    assert by_udid["LOCKED001"].state is DeviceState.LOCKED
    assert by_udid["LOCKED001"].name is None


# ---------------------------------------------------------------------------
# Scenario 3: timeout / missing executable -> [] without crashing (1.1)
# ---------------------------------------------------------------------------

def test_enumerate_timeout_returns_empty_list() -> None:
    """A timeout on `idevice_id -l` yields an empty list, not an exception (Req 1.1)."""
    runner = FakeRunner(
        list_exc=subprocess.TimeoutExpired(cmd=["idevice_id", "-l"], timeout=5.0),
    )
    connector = DeviceConnector(runner=runner)

    devices = connector.enumerate_devices(timeout_s=5.0)

    assert devices == []
    # Only the listing command was attempted before timing out.
    assert runner.calls == [_Call(cmd=["idevice_id", "-l"], timeout=5.0)]


def test_enumerate_missing_executable_returns_empty_list() -> None:
    """A missing `idevice_id` executable yields an empty list (Req 1.1)."""
    runner = FakeRunner(list_exc=FileNotFoundError("idevice_id"))
    connector = DeviceConnector(runner=runner)

    devices = connector.enumerate_devices(timeout_s=5.0)

    assert devices == []
    assert runner.calls[0].cmd == ["idevice_id", "-l"]
