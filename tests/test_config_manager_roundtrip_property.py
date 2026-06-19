"""Property-based test for Config_Manager serialization round-trip (Task 2.4).

Uses Hypothesis to verify that any valid :class:`AppConfig` survives a
``save`` -> ``load`` round-trip unchanged.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 9.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.config_manager import ConfigManager
from spotlight_monitor.models import AppConfig, FilterRule, ParseRule

# A small pool of regex patterns guaranteed to compile. Using a fixed pool (or
# ``None`` where allowed) ensures ``ConfigManager.save`` never rejects the
# generated config for an uncompilable pattern (Req 5.5), so the round-trip is
# always exercised on a *valid* configuration.
_VALID_PATTERNS: tuple[str, ...] = (
    r"progress[^0-9]*([0-9]{1,3})\s*%",
    r"([0-9]+)\s*/\s*([0-9]+)\s*items",
    r"indexing\s+([0-9.]+)",
    r"^spotlight",
    r"mds.*done",
    r"[a-z]+",
)

# Plain, well-behaved text for ids and keywords: keeps the generated data
# readable and avoids surprises, while JSON itself round-trips any string.
_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=0x2FFF),
    min_size=0,
    max_size=20,
)

_filter_rules = st.lists(
    st.builds(
        FilterRule,
        id=st.text(min_size=1, max_size=20),
        keywords=st.lists(_text, max_size=5).map(tuple),
        pattern=st.one_of(st.none(), st.sampled_from(_VALID_PATTERNS)),
        enabled=st.booleans(),
    ),
    max_size=5,
)

_parse_rules = st.lists(
    st.builds(
        ParseRule,
        id=st.text(min_size=1, max_size=20),
        pattern=st.sampled_from(_VALID_PATTERNS),
        # Finite, positive floats so scale_max survives the float coercion on
        # load and equality holds (Req: scale_max is stored/loaded as float).
        scale_max=st.floats(
            min_value=1e-3,
            max_value=1e9,
            allow_nan=False,
            allow_infinity=False,
        ),
    ),
    max_size=5,
)

_app_configs = st.builds(
    AppConfig,
    filter_rules=_filter_rules.map(tuple),
    parse_rules=_parse_rules.map(tuple),
    case_sensitive=st.booleans(),
)


# Feature: iphone-spotlight-indexing-monitor, Property 9: 配置序列化往返
@settings(max_examples=100)
@given(config=_app_configs)
def test_property_9_config_serialization_roundtrip(config: AppConfig) -> None:
    """Any valid AppConfig saved then loaded yields an equivalent object.

    Verifies filter_rules, parse_rules and case_sensitive are all preserved
    across a ``save`` -> ``load`` round-trip (Req 5.1, 5.4). Frozen dataclasses
    provide structural equality, so comparing the loaded config to the original
    with ``==`` checks every field (including the nested rule tuples).
    """
    manager = ConfigManager()

    # Use a unique temp file inside the test body to avoid Hypothesis's
    # function-scoped-fixture health check (a single fixture would be shared
    # across all generated examples).
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "config.json"

        manager.save(path, config)
        loaded = manager.load(path)

    # No load errors should be reported for a configuration we just wrote.
    assert manager.last_load_errors == []
    assert loaded == config
