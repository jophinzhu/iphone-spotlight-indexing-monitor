"""Smoke tests for the core data models and package import (Task 1).

These verify the shared types exist with the field signatures defined in
design.md and that they are immutable (frozen dataclasses), since the rest of
the system relies on them as side-effect-free shared types.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

import spotlight_monitor
from spotlight_monitor.models import (
    AppConfig,
    DeviceInfo,
    DeviceState,
    ExportMode,
    FilterRule,
    IndexingProgress,
    ParseRule,
    RawLogLine,
    StreamEvent,
)


def test_package_imports_cleanly():
    assert spotlight_monitor.__version__


def test_enum_members():
    assert {s.value for s in DeviceState} == {
        "connected_paired",
        "connected_unpaired",
        "locked",
    }
    assert {e.value for e in StreamEvent} == {
        "line",
        "disconnected",
        "process_exited",
    }
    assert {m.value for m in ExportMode} == {"raw", "filtered"}


def test_device_info_fields():
    info = DeviceInfo(udid="abc123", name="iPhone", state=DeviceState.CONNECTED_PAIRED)
    assert info.udid == "abc123"
    assert info.name == "iPhone"
    assert info.state is DeviceState.CONNECTED_PAIRED


def test_raw_log_line_carries_timestamp():
    now = datetime(2024, 1, 1, 12, 0, 0)
    line = RawLogLine(text="hello", received_at=now)
    assert line.text == "hello"
    assert line.received_at == now


def test_filter_rule_defaults():
    rule = FilterRule(id="r1")
    assert rule.keywords == ()
    assert rule.pattern is None
    assert rule.enabled is True


def test_parse_rule_defaults():
    rule = ParseRule(id="p1", pattern="(\\d+)%")
    assert rule.scale_max == 100.0


def test_indexing_progress_fields():
    now = datetime(2024, 1, 1, 12, 0, 0)
    progress = IndexingProgress(percent=42.0, source_line="indexing 42%", observed_at=now)
    assert progress.percent == 42.0
    assert progress.source_line == "indexing 42%"
    assert progress.observed_at == now


def test_app_config_defaults():
    config = AppConfig()
    assert config.filter_rules == ()
    assert config.parse_rules == ()
    assert config.case_sensitive is False


@pytest.mark.parametrize(
    "instance",
    [
        DeviceInfo(udid="u", name=None, state=DeviceState.LOCKED),
        RawLogLine(text="t", received_at=datetime(2024, 1, 1)),
        FilterRule(id="r"),
        ParseRule(id="p", pattern="x"),
        IndexingProgress(percent=1.0, source_line="s", observed_at=datetime(2024, 1, 1)),
        AppConfig(),
    ],
)
def test_models_are_frozen(instance):
    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "mutated")
