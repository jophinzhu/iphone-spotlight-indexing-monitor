"""Property-based test for Log_Filter case-sensitivity (Task 3.4).

Uses Hypothesis to verify the case-sensitivity metamorphic property: the set of
lines matched in case-insensitive mode is a superset of the set matched in
case-sensitive mode. Equivalently, for every line, a case-sensitive match
implies a case-insensitive match — switching from insensitive to sensitive
never turns a non-match into a match.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 3.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.models import FilterRule
from spotlight_monitor.log_filter import LogFilter

# Keywords are drawn from a mix of letter cases so that case-sensitivity is
# actually exercised (a keyword like "Spotlight" matches "spotlight" only in
# insensitive mode).
_keyword_strategy = st.text(
    alphabet="abcDEF gMNpQ123",
    min_size=1,
    max_size=6,
)

# Simple, always-valid regex patterns. Regex case-sensitivity is controlled by
# the IGNORECASE flag the filter applies, so the property must hold for
# patterns too. Patterns include letters in mixed case to exercise the flag.
_pattern_strategy = st.sampled_from(
    [
        None,
        None,
        "Index",
        "progress",
        "MDS",
        "[0-9]+",
        "spot.*light",
        "Done",
        r"\bP\w+",
    ]
)


@st.composite
def _filter_rules(draw: st.DrawFn) -> tuple[FilterRule, ...]:
    """Generate a small set of FilterRule with keywords and/or simple patterns."""
    count = draw(st.integers(min_value=1, max_value=4))
    rules: list[FilterRule] = []
    for i in range(count):
        keywords = tuple(
            draw(st.lists(_keyword_strategy, min_size=0, max_size=3))
        )
        pattern = draw(_pattern_strategy)
        enabled = draw(st.booleans())
        # Avoid a rule that has neither keywords nor a pattern (never matches);
        # keep it valid but meaningful by forcing at least one keyword then.
        if not keywords and pattern is None:
            keywords = (draw(_keyword_strategy),)
        rules.append(
            FilterRule(id=f"rule-{i}", keywords=keywords, pattern=pattern, enabled=enabled)
        )
    return tuple(rules)


# Log lines drawn from a mixed-case alphabet plus arbitrary text, covering
# empty lines and lines with/without keyword fragments in varied cases.
_line_strategy = st.one_of(
    st.text(alphabet="abcDEF gMNpQ123/%", max_size=40),
    st.text(max_size=40),
)


# Feature: iphone-spotlight-indexing-monitor, Property 3: 大小写敏感性元变换
@settings(max_examples=100)
@given(
    rules=_filter_rules(),
    lines=st.lists(_line_strategy, max_size=20),
)
def test_property_3_case_sensitivity_metamorphic(
    rules: tuple[FilterRule, ...], lines: list[str]
) -> None:
    """Case-insensitive match set is a superset of the case-sensitive set (Req 3.5).

    Two filters share the SAME rules: one case-sensitive, one case-insensitive.
    For every line, a case-sensitive match implies a case-insensitive match.
    """
    sensitive = LogFilter(rules, case_sensitive=True)
    insensitive = LogFilter(rules, case_sensitive=False)

    for line in lines:
        if sensitive.matches(line):
            assert insensitive.matches(line), (
                "case-sensitive match must imply case-insensitive match: "
                f"line={line!r}, rules={rules!r}"
            )

    # Equivalent set-level statement: the insensitive match set is a superset of
    # the sensitive match set.
    sensitive_matched = {line for line in lines if sensitive.matches(line)}
    insensitive_matched = {line for line in lines if insensitive.matches(line)}
    assert sensitive_matched <= insensitive_matched
