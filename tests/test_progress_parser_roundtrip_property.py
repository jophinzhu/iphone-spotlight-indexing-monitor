"""Property-based test for Progress_Parser parse round-trip (Task 4.3).

Uses Hypothesis to verify that for any percentage value ``p`` in ``[0, 100]``,
constructing a log line that matches a percentage parse-rule template and
embeds ``p`` yields a parsed ``IndexingProgress.percent`` equal to the embedded
value (within a small floating-point tolerance).

Design reference: ``design.md`` -> "Correctness Properties" -> Property 5.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.models import ParseRule, RawLogLine
from spotlight_monitor.progress_parser import ProgressParser

# The generic percentage parse rule from the default configuration / design:
# captures 1-3 integer digits with optional decimals followed by '%',
# normalized against a scale of 100.
_PERCENT_RULE = ParseRule(
    id="percent-generic",
    pattern=r"progress[^0-9]*([0-9]{1,3}(?:\.[0-9]+)?)\s*%",
    scale_max=100.0,
)


# Feature: iphone-spotlight-indexing-monitor, Property 5: 进度解析往返
@settings(max_examples=100)
@given(p=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False))
def test_property_5_progress_parse_round_trip(p: float) -> None:
    """A percentage embedded in a matching log line round-trips through parsing.

    The value ``p`` is formatted to two decimals so its integer part is at most
    three digits (``p <= 100`` => ``"100.00"``), satisfying the pattern's
    ``[0-9]{1,3}(?:\\.[0-9]+)?`` capture. Because the rule's ``scale_max`` is
    100, normalization is the identity on values already in ``[0, 100]``, so the
    parsed percent must equal the embedded (formatted) value (Req 4.1, 4.2).
    """
    formatted = f"{p:.2f}"
    expected = float(formatted)

    line = RawLogLine(
        text=f"spotlight progress: {formatted} %",
        received_at=datetime(2024, 1, 1, 12, 0, 0),
    )

    parser = ProgressParser([_PERCENT_RULE])
    result = parser.parse(line)

    assert result is not None
    assert result.percent == pytest.approx(expected, abs=1e-6)
