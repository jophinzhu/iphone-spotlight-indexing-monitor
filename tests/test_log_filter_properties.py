"""Property-based tests for Log_Filter (Task 3.2).

Uses Hypothesis to verify universal correctness properties of
:class:`spotlight_monitor.log_filter.LogFilter`. See ``design.md`` ->
"Correctness Properties".
"""

from __future__ import annotations

import string
from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.log_filter import LogFilter
from spotlight_monitor.models import FilterRule, RawLogLine

# A small pool of fragments that may be used as rule keywords AND embedded into
# generated log lines, so that matches and non-matches both occur frequently.
_FRAGMENTS = ["spot", "index", "prog", "mds", "abc", "xyz", "ZZ", "Idx"]

_BASE_TIME = datetime(2024, 1, 1, 0, 0, 0)


@st.composite
def _filter_rules(draw: st.DrawFn) -> list[FilterRule]:
    """Generate a varied set of FilterRule (keywords, optional pattern, enabled)."""
    count = draw(st.integers(min_value=0, max_value=4))
    rules: list[FilterRule] = []
    for i in range(count):
        keywords = tuple(
            draw(
                st.lists(
                    st.sampled_from(_FRAGMENTS),
                    min_size=0,
                    max_size=3,
                    unique=True,
                )
            )
        )
        # Keep patterns simple/valid (or None) to avoid re.error noise.
        pattern = draw(
            st.one_of(
                st.none(),
                st.sampled_from([None, "prog[0-9]+", "mds.*done", "[Ii]ndex"]),
            )
        )
        enabled = draw(st.booleans())
        rules.append(
            FilterRule(id=f"rule-{i}", keywords=keywords, pattern=pattern, enabled=enabled)
        )
    return rules


@st.composite
def _log_lines(draw: st.DrawFn) -> list[RawLogLine]:
    """Generate RawLogLine list with arbitrary text and timestamps.

    Lines sometimes embed known fragments to exercise meaningful matching.
    """
    count = draw(st.integers(min_value=0, max_value=12))
    lines: list[RawLogLine] = []
    for i in range(count):
        free_text = draw(st.text(alphabet=string.printable, min_size=0, max_size=20))
        # Occasionally embed one or more known fragments into the line.
        embedded = draw(
            st.lists(st.sampled_from(_FRAGMENTS), min_size=0, max_size=2)
        )
        parts = [free_text, *embedded]
        draw(st.randoms()).shuffle(parts)
        text = " ".join(parts)
        received_at = _BASE_TIME + timedelta(seconds=i)
        lines.append(RawLogLine(text=text, received_at=received_at))
    return lines


# Feature: iphone-spotlight-indexing-monitor, Property 1: 过滤正确性（保留当且仅当匹配）
# Validates: Requirements 3.1, 3.2, 3.3
@settings(max_examples=200)
@given(lines=_log_lines(), rules=_filter_rules(), case_sensitive=st.booleans())
def test_filtering_correctness_keep_iff_match(
    lines: list[RawLogLine], rules: list[FilterRule], case_sensitive: bool
) -> None:
    log_filter = LogFilter(rules, case_sensitive=case_sensitive)
    output = list(log_filter.filter_stream(lines))

    # (1) Output is an order-preserving subsequence of the input: walk the input
    # once, advancing an output pointer; every output element must be matched in
    # order against the input.
    out_idx = 0
    for line in lines:
        if out_idx < len(output) and output[out_idx] is line:
            out_idx += 1
    assert out_idx == len(output), "output is not an order-preserving subsequence of input"

    # (2) keep-iff-match: a line is present in the output if and only if
    # filter.matches(line.text) is True.
    for line in lines:
        is_kept = any(o is line for o in output)
        assert is_kept == log_filter.matches(line.text)
