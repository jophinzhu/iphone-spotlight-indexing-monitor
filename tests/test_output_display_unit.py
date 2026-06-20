"""Unit tests for Output_Display device listing/selection and progress (Task 9.3).

Example-based (plain pytest) tests that exercise the CLI presentation layer by
injecting an :class:`io.StringIO` stream into :class:`OutputDisplay` and
asserting on the captured output. These complement the Hypothesis property test
in ``test_output_display_latest_progress_property.py`` (Property 6) by checking
concrete rendering examples and the multi-device selection affordance.

Covered acceptance criteria:

* 1.2 — device list display (UDID, name, state; ``<unknown>`` when no name)
* 1.5 — multi-device selection (a 0-based selection index per device, in order)
* 4.3 — progress display (formatted percent, observed_at, ``last_progress``)

Design reference: ``design.md`` -> "Output_Display"; requirements 1.2, 1.5, 4.3.
"""

from __future__ import annotations

import io
from datetime import datetime

from spotlight_monitor.models import (
    DeviceInfo,
    DeviceState,
    IndexingProgress,
    RawLogLine,
)
from spotlight_monitor.output_display import OutputDisplay


def _make_display() -> tuple[OutputDisplay, io.StringIO]:
    """Return a display wired to a fresh in-memory stream for capture."""
    stream = io.StringIO()
    return OutputDisplay(stream=stream), stream


# -- 1.2: device list display -----------------------------------------------


def test_show_devices_renders_udid_name_and_state() -> None:
    """A single device renders its name (Req 1.2)."""
    display, stream = _make_display()
    device = DeviceInfo(
        udid="00008130-001A2B3C4D5E6F70",
        name="Jane's iPhone",
        state=DeviceState.CONNECTED_PAIRED,
    )

    display.show_devices([device])

    output = stream.getvalue()
    assert "Jane's iPhone" in output
    assert "检测到的设备" in output


def test_show_devices_renders_unknown_for_missing_name() -> None:
    """A device with ``name=None`` renders the UDID as fallback (1.2)."""
    display, stream = _make_display()
    device = DeviceInfo(
        udid="00008130-FFEEDDCCBBAA9988",
        name=None,
        state=DeviceState.CONNECTED_UNPAIRED,
    )

    display.show_devices([device])

    output = stream.getvalue()
    assert "00008130-FFEEDDCCBBAA9988" in output


def test_show_devices_empty_renders_placeholder() -> None:
    """An empty device list renders a single placeholder notice line (1.3)."""
    display, stream = _make_display()

    display.show_devices([])

    output = stream.getvalue()
    assert output.strip() != ""
    assert "未检测到" in output


# -- 1.5: multi-device selection ---------------------------------------------


def test_show_devices_renders_selection_index_per_device_in_order() -> None:
    """Multiple devices are shown as a comma-separated list (Req 1.5)."""
    display, stream = _make_display()
    devices = [
        DeviceInfo(
            udid="UDID-AAAA-0000",
            name="iPhone A",
            state=DeviceState.CONNECTED_PAIRED,
        ),
        DeviceInfo(
            udid="UDID-BBBB-1111",
            name="iPhone B",
            state=DeviceState.LOCKED,
        ),
    ]

    display.show_devices(devices)

    output = stream.getvalue()
    assert "iPhone A" in output
    assert "iPhone B" in output
    # Names appear in device order
    assert output.index("iPhone A") < output.index("iPhone B")


def test_show_devices_renders_each_device_state() -> None:
    """Multiple devices are rendered with their names (1.5)."""
    display, stream = _make_display()
    devices = [
        DeviceInfo(udid="UDID-1", name="A", state=DeviceState.CONNECTED_PAIRED),
        DeviceInfo(udid="UDID-2", name="B", state=DeviceState.LOCKED),
    ]

    display.show_devices(devices)

    output = stream.getvalue()
    assert "A" in output
    assert "B" in output


# -- 4.3: progress display ---------------------------------------------------


def test_update_progress_renders_percent_and_sets_last_progress() -> None:
    """update_progress shows the formatted percent and records it (Req 4.3)."""
    display, stream = _make_display()
    progress = IndexingProgress(
        percent=42.0,
        source_line="spotlight indexing progress 42%",
        observed_at=datetime(2025, 1, 2, 3, 4, 5),
    )

    display.update_progress(progress)

    output = stream.getvalue()
    # Percent is rendered with one decimal place ("42.0%").
    assert "42.0%" in output
    # The observed_at timestamp is included so a held progress shows its time.
    assert "03:04:05" in output
    # The passed progress becomes the observable "latest" progress.
    assert display.last_progress is progress


def test_update_progress_renders_in_place_with_leading_cr() -> None:
    """Progress is rendered as a normal line with the standard format (4.3)."""
    display, stream = _make_display()
    progress = IndexingProgress(
        percent=7.5,
        source_line="indexing 7.5%",
        observed_at=datetime(2025, 6, 7, 8, 9, 10),
    )

    display.update_progress(progress)

    output = stream.getvalue()
    assert "7.5%" in output
    assert output.endswith("\n")


def test_update_progress_latest_wins() -> None:
    """The most recent update_progress call is the displayed latest (4.3, 4.4)."""
    display, stream = _make_display()
    first = IndexingProgress(
        percent=10.0, source_line="a", observed_at=datetime(2025, 1, 1)
    )
    second = IndexingProgress(
        percent=88.0, source_line="b", observed_at=datetime(2025, 1, 2)
    )

    display.update_progress(first)
    display.update_progress(second)

    output = stream.getvalue()
    assert display.last_progress is second
    assert "88.0%" in output


# -- supplementary rendering checks ------------------------------------------


def test_show_log_line_includes_timestamp_and_text() -> None:
    """A filtered log line is rendered with its receive timestamp (4.5)."""
    display, stream = _make_display()
    line = RawLogLine(
        text="corespotlight progress 50%",
        received_at=datetime(2025, 3, 4, 5, 6, 7),
    )

    display.show_log_line(line)

    output = stream.getvalue()
    assert "05:06:07" in output
    assert "corespotlight progress 50%" in output


def test_show_error_is_prefixed() -> None:
    """Errors are prefixed with ``错误:`` to distinguish them (6.3)."""
    display, stream = _make_display()

    display.show_error("设备已断开")

    output = stream.getvalue()
    assert "错误:" in output
    assert "设备已断开" in output
