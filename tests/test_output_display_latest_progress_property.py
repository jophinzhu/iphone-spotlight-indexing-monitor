"""Property-based test for Output_Display latest-progress retention (Task 9.2).

Uses Hypothesis to verify that, at any moment, the progress the display
considers "latest" (``OutputDisplay.last_progress``) equals the most recent
successfully parsed ``IndexingProgress`` seen up to that point, and is ``None``
before the first successful parse.

We model a stream of per-line parse *results*: each element is either a
successfully parsed ``IndexingProgress`` (the parser extracted a value) or
``None`` (no progress was parsed for that line). Successful results are pushed
via :meth:`OutputDisplay.update_progress`; ``None`` results simulate "no new
progress parsed" and must not change the latest progress (we optionally call
:meth:`OutputDisplay.redraw_progress`, which holds — never mutates — the
latest progress).

Design reference: ``design.md`` -> "Correctness Properties" -> Property 6.
"""

from __future__ import annotations

import io

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.models import IndexingProgress
from spotlight_monitor.output_display import OutputDisplay

# A strategy producing valid IndexingProgress values: a finite percent in
# [0, 100], an arbitrary source line, and a naive datetime.
_progress_strategy = st.builds(
    IndexingProgress,
    percent=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    source_line=st.text(),
    observed_at=st.datetimes(),
)

# Each element of the sequence is either a successful parse (IndexingProgress)
# or no progress parsed for that line (None).
_results_strategy = st.lists(st.one_of(st.none(), _progress_strategy))


# Feature: iphone-spotlight-indexing-monitor, Property 6: 最近进度保持
@settings(max_examples=100)
@given(results=_results_strategy)
def test_property_6_latest_progress_retention(
    results: list[IndexingProgress | None],
) -> None:
    """At any step, last_progress equals the most recent successful parse.

    Starting from a fresh display (nothing parsed => ``last_progress is None``),
    we replay the parse-result sequence. After each element we assert that
    ``last_progress`` equals the last non-``None`` element observed so far, or
    ``None`` if no successful parse has occurred yet (Req 4.4).
    """
    display = OutputDisplay(stream=io.StringIO())

    # No successful parse yet => no progress is displayed.
    assert display.last_progress is None

    expected: IndexingProgress | None = None
    for result in results:
        if result is not None:
            # A successful parse updates and displays the latest progress.
            display.update_progress(result)
            expected = result
        else:
            # No new progress parsed: the display must keep showing the most
            # recent progress (redraw must not mutate the retained value).
            display.redraw_progress()

        assert display.last_progress is expected
