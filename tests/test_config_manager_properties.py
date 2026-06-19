"""Property-based tests for Config_Manager (Task 2.2).

Uses Hypothesis to verify universal properties of ``ConfigManager.validate``
that should hold across arbitrary regex-pattern strings.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 10.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.config_manager import ConfigManager
from spotlight_monitor.models import AppConfig, ParseRule


def _compiles(pattern: str) -> bool:
    """Return True iff ``pattern`` compiles as a regex via :func:`re.compile`."""
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


# Feature: iphone-spotlight-indexing-monitor, Property 10: 正则校验正确性 — validate 接受 pattern 当且仅当其可被成功编译为正则
@settings(max_examples=100)
@given(pattern=st.text())
def test_property_10_regex_validation_correctness(pattern: str) -> None:
    """validate accepts a pattern iff it can be compiled as a regex (Req 5.5).

    The single parse rule under test carries the generated ``pattern``; no
    other rules are present so the result isolates this rule's validation.
    Arbitrary ``st.text()`` produces both compilable and non-compilable
    samples, exercising both directions of the biconditional.
    """
    rule_id = "rule-under-test"
    config = AppConfig(
        filter_rules=(),
        parse_rules=(ParseRule(id=rule_id, pattern=pattern),),
        case_sensitive=False,
    )

    errors = ConfigManager.validate(config)

    if _compiles(pattern):
        # Compilable pattern => accepted => no errors reported.
        assert errors == []
    else:
        # Non-compilable pattern => rejected => an error referencing the rule.
        assert errors != []
        assert any(rule_id in message for message in errors)
