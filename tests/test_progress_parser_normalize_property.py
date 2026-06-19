"""Property-based test for Progress_Parser normalization interval (Task 4.2).

Uses Hypothesis to verify that :meth:`ProgressParser.normalize` always returns
a value within the closed interval ``[0, 100]`` regardless of the input value
or scale maximum (including non-positive scales, negatives, and very large
magnitudes). This underpins the data invariant that any successfully parsed
``IndexingProgress.percent`` satisfies ``0 <= percent <= 100``.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 4.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.progress_parser import ProgressParser


# Feature: iphone-spotlight-indexing-monitor, Property 4: 进度规范化区间不变量
@settings(max_examples=100)
@given(
    value=st.floats(allow_nan=False, allow_infinity=False),
    scale_max=st.floats(allow_nan=False, allow_infinity=False),
)
def test_property_4_normalize_interval_invariant(
    value: float,
    scale_max: float,
) -> None:
    """normalize always yields a result within the closed interval [0, 100].

    For any finite ``value`` (negative, zero, or very large) and any finite
    ``scale_max`` (non-positive, zero, negative, or large positive), the
    normalized result must satisfy ``0.0 <= result <= 100.0`` (Req 4.2).
    """
    result = ProgressParser.normalize(value, scale_max)

    assert 0.0 <= result <= 100.0
