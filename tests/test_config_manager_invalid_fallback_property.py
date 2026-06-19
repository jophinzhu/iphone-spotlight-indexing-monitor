"""Property-based test for Config_Manager invalid-config fallback (Task 2.5).

Uses Hypothesis to verify that for *any* invalid or corrupt configuration file
content, :meth:`ConfigManager.load`:

1. does not raise an uncaught exception (the call simply completing is the
   assertion of "no uncaught exception"),
2. returns the built-in default configuration, and
3. records the problem(s) on the ``last_load_errors`` side channel.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 11.

Generator invalidity guarantee
-------------------------------
To keep this property *sound* every generated content string is constructed so
that it can **never** deserialize and validate into a usable ``AppConfig``.
There is therefore no need to special-case "accidentally valid" examples: all
four branches below are guaranteed-invalid by construction, so assertions (2)
and (3) hold for every example.

* ``_non_object_json``  - valid JSON whose root is a scalar/array (never the
  required JSON object), so deserialization rejects it on the root type.
* ``_wrong_keys_json``  - a JSON object whose key set is drawn from keys that
  *exclude* the three required top-level keys, so the key set can never match.
* ``_wrong_type_json``  - a JSON object with exactly the required keys but a
  wrong-typed value (e.g. ``case_sensitive`` is never a bool), rejected on type.
* ``_garbage_text``     - text that begins with ``@`` (and other non-JSON
  noise), which can never start a valid JSON value, so JSON parsing always
  fails regardless of the remainder.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.config_manager import ConfigManager

# The exact set of top-level keys a valid configuration object must have.
_REQUIRED_KEYS = {"case_sensitive", "filter_rules", "parse_rules"}


# Branch 1: valid JSON, but the root is a scalar or array rather than the
# required object. Deserialization rejects any non-dict root -> fallback.
_non_object_json = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=40),
    st.booleans(),
    st.none(),
    st.lists(st.integers(), max_size=5),
).map(json.dumps)

# Branch 2: a JSON object whose keys deliberately exclude every required key,
# so the top-level key set can never equal the required set -> fallback.
_wrong_keys_json = st.dictionaries(
    keys=st.text(max_size=10).filter(lambda k: k not in _REQUIRED_KEYS),
    values=st.one_of(st.integers(), st.text(max_size=10), st.booleans(), st.none()),
    max_size=5,
).map(json.dumps)

# Branch 3: a JSON object with exactly the required keys but wrong-typed values
# (``case_sensitive`` is never a bool, the rule fields are never lists), so
# deserialization rejects it on a type check -> fallback.
_wrong_type_json = st.fixed_dictionaries(
    {
        "case_sensitive": st.one_of(st.integers(), st.text(max_size=10), st.none()),
        "filter_rules": st.one_of(st.text(max_size=10), st.integers()),
        "parse_rules": st.one_of(st.text(max_size=10), st.integers()),
    }
).map(json.dumps)

# Branch 4: non-JSON garbage. A leading ``@`` can never begin a valid JSON
# value, so ``json.loads`` always fails regardless of the trailing text.
_garbage_text = st.text(max_size=80).map(lambda s: "@@@ not-json " + s)

_invalid_content = st.one_of(
    _non_object_json,
    _wrong_keys_json,
    _wrong_type_json,
    _garbage_text,
)


# Feature: iphone-spotlight-indexing-monitor, Property 11: 无效配置回退默认（边界）
@settings(max_examples=100)
@given(content=_invalid_content)
def test_property_11_invalid_config_falls_back_to_default(content: Any) -> None:
    """Any invalid/corrupt config content loads as the default and reports errors.

    For every guaranteed-invalid content string the loader must (Req 5.3):

    1. complete without raising an uncaught exception,
    2. return a configuration equal to ``ConfigManager.default_config()``, and
    3. record at least one message on ``last_load_errors``.
    """
    manager = ConfigManager()

    # Unique temp file inside the test body avoids Hypothesis's
    # function-scoped-fixture health check (a fixture would be shared across
    # all generated examples).
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "config.json"
        path.write_text(content, encoding="utf-8")

        # Assertion 1: the call itself completing is proof that no uncaught
        # exception escaped ``load`` for invalid input.
        loaded = manager.load(path)

    # Assertion 2: invalid content falls back to the built-in default config.
    assert loaded == ConfigManager.default_config()

    # Assertion 3: the problem was reported via the side channel.
    assert manager.last_load_errors, (
        "expected at least one load error for invalid configuration content, "
        f"but last_load_errors was empty (content={content!r})"
    )
