"""Property-based test for Log_Filter default-keyword matching (Task 3.3).

Uses Hypothesis to verify that any log line containing one of the built-in
default keywords (in any case variant) is matched by the default filter rules
when running in case-insensitive mode.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 2.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.config_manager import ConfigManager
from spotlight_monitor.log_filter import LogFilter


def _recase(keyword: str, toggles: list[bool]) -> str:
    """Return ``keyword`` with each character upper/lowercased per ``toggles``."""
    return "".join(
        ch.upper() if upper else ch.lower()
        for ch, upper in zip(keyword, toggles)
    )


# Feature: iphone-spotlight-indexing-monitor, Property 2: 默认关键字大小写不敏感匹配
@settings(max_examples=100)
@given(
    keyword=st.sampled_from(ConfigManager.DEFAULT_KEYWORDS),
    case_toggles=st.lists(st.booleans(), min_size=20, max_size=20),
    prefix=st.text(),
    suffix=st.text(),
)
def test_property_2_default_keyword_case_insensitive_match(
    keyword: str,
    case_toggles: list[bool],
    prefix: str,
    suffix: str,
) -> None:
    """A line containing a default keyword (any case) matches case-insensitively.

    Each character of a randomly chosen default keyword is recased to produce a
    mixed-case variant, then embedded between arbitrary prefix/suffix text. The
    default filter rules in case-insensitive mode must judge such a line as a
    match (Req 3.4).
    """
    variant = _recase(keyword, case_toggles)
    line = f"{prefix}{variant}{suffix}"

    log_filter = LogFilter(
        ConfigManager.default_config().filter_rules,
        case_sensitive=False,
    )

    assert log_filter.matches(line) is True
