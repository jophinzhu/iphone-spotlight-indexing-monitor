"""Property-based test for rule hot-reload equivalence (Task 11.4).

Uses Hypothesis to verify that applying a new rule set mid-monitoring via
:meth:`IndexingMonitor.apply_rules` makes the processing of every *subsequent*
log line identical to processing that same line from scratch with a fresh
monitor built directly from the new rules. In other words, hot-reloading the
rules is equivalent to restarting with the new rules (the underlying log stream
is never restarted, only the rule snapshot is swapped — Req 5.6).

Design reference: ``design.md`` -> "Correctness Properties" -> Property 12.
"""

from __future__ import annotations

from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.indexing_monitor import IndexingMonitor
from spotlight_monitor.models import AppConfig, FilterRule, ParseRule, RawLogLine

# A small pool of regex patterns guaranteed to compile, mirroring the config
# round-trip test. Using a fixed pool (or ``None`` where allowed) keeps every
# generated config valid, so both the old and new monitors construct without
# error and the equivalence comparison always runs on usable rule sets.
_VALID_PATTERNS: tuple[str, ...] = (
    r"progress[^0-9]*([0-9]{1,3}(?:\.[0-9]+)?)\s*%",
    r"([0-9]+)\s*/\s*([0-9]+)\s*items",
    r"indexing\s+([0-9.]+)",
    r"^spotlight",
    r"mds.*done",
    r"[a-z]+",
)

# Plain, readable text for rule ids and keywords. The keyword pool is biased
# toward the default Spotlight keywords so generated filter rules frequently
# match the generated log lines (giving meaningful, non-trivially-empty
# coverage rather than everything being filtered out).
_keyword = st.sampled_from(
    ["spotlight", "indexing", "progress", "mds", "corespotlight", "items", "done"]
)

_filter_rules = st.lists(
    st.builds(
        FilterRule,
        id=st.text(min_size=1, max_size=12),
        keywords=st.lists(_keyword, max_size=4).map(tuple),
        pattern=st.one_of(st.none(), st.sampled_from(_VALID_PATTERNS)),
        enabled=st.booleans(),
    ),
    max_size=4,
)

_parse_rules = st.lists(
    st.builds(
        ParseRule,
        id=st.text(min_size=1, max_size=12),
        pattern=st.sampled_from(_VALID_PATTERNS),
        # Finite, positive scale_max so normalization is well-defined.
        scale_max=st.floats(
            min_value=1e-3,
            max_value=1e9,
            allow_nan=False,
            allow_infinity=False,
        ),
    ),
    max_size=4,
)

_app_configs = st.builds(
    AppConfig,
    filter_rules=_filter_rules.map(tuple),
    parse_rules=_parse_rules.map(tuple),
    case_sensitive=st.booleans(),
)

# Log-line text biased toward content the rules can match: progress percentages,
# item fractions and stray keywords are mixed with arbitrary free text so some
# lines parse, some only match the filter, and some are dropped entirely.
_line_text = st.one_of(
    st.builds(
        lambda v: f"spotlight progress {v}%",
        st.integers(min_value=0, max_value=100),
    ),
    st.builds(
        lambda a, b: f"mds indexing {a} / {b} items",
        st.integers(min_value=0, max_value=500),
        st.integers(min_value=1, max_value=500),
    ),
    st.builds(lambda v: f"indexing {v}", st.floats(
        min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
    )),
    st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FFF),
        min_size=0,
        max_size=40,
    ),
)

# Naive datetimes for received_at (no tz); equality of IndexingProgress depends
# on observed_at == received_at being identical across both monitors, which it
# is because we feed the *same* RawLogLine objects to both.
_received_at = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
)

_log_lines = st.lists(
    st.builds(RawLogLine, text=_line_text, received_at=_received_at),
    min_size=1,
    max_size=20,
)


# Feature: iphone-spotlight-indexing-monitor, Property 12: 规则热更新等价
@settings(max_examples=100)
@given(
    old_config=_app_configs,
    new_config=_app_configs,
    lines=_log_lines,
    data=st.data(),
)
def test_property_12_rule_hot_reload_equivalence(
    old_config: AppConfig,
    new_config: AppConfig,
    lines: list[RawLogLine],
    data: st.DataObject,
) -> None:
    """Hot-reloading rules equals restarting with the new rules for later lines.

    A ``monitor_hot`` is created under ``old_config`` and processes a prefix of
    the lines (simulating live monitoring). It then hot-reloads ``new_config``
    via :meth:`IndexingMonitor.apply_rules`. For every *subsequent* line, the
    result of ``monitor_hot.process_line`` must equal the result of processing
    that same line on a brand-new ``monitor_fresh`` built directly from
    ``new_config`` (Req 5.6). ``process_line`` is stateless per line w.r.t. the
    active snapshot, so a fresh monitor reflects "from scratch with the new
    rules" exactly. Both compare ``IndexingProgress | None`` results; equality
    holds because the same ``RawLogLine`` objects are fed to both, preserving
    ``source_line`` and ``observed_at``.
    """
    # Split point: how many lines are processed under the OLD rules before the
    # hot reload. ``0..len(lines)`` lets the prefix be empty or the whole list.
    split = data.draw(st.integers(min_value=0, max_value=len(lines)))
    prefix, subsequent = lines[:split], lines[split:]

    monitor_hot = IndexingMonitor(config=old_config)

    # Simulate monitoring under the old rules: process the prefix (results are
    # irrelevant to the property, but exercise the live-stream state).
    for line in prefix:
        monitor_hot.process_line(line)

    # Hot reload to the new rules without restarting the stream (Req 5.6).
    monitor_hot.apply_rules(new_config)

    # A fresh monitor built directly from the new rules == "from scratch".
    monitor_fresh = IndexingMonitor(config=new_config)

    for line in subsequent:
        hot_result = monitor_hot.process_line(line)
        fresh_result = monitor_fresh.process_line(line)
        assert hot_result == fresh_result
