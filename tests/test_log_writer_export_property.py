"""Property-based test for Log_Writer export modes (Task 6.2).

Uses Hypothesis to verify Property 13 (导出模式正确性) of
:class:`spotlight_monitor.log_writer.LogWriter`. See ``design.md`` ->
"Correctness Properties".
"""

from __future__ import annotations

import string
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.log_filter import LogFilter
from spotlight_monitor.log_writer import LogWriter, _deserialize
from spotlight_monitor.models import ExportMode, FilterRule, RawLogLine

# Fragments shared between generated rule keywords and generated log line text
# so both matches and non-matches occur frequently.
_FRAGMENTS = ["spot", "index", "prog", "mds", "abc", "xyz", "ZZ", "Idx"]


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
    """Generate a RawLogLine list with arbitrary text and naive datetimes.

    ``text`` excludes newlines so it survives the line-oriented on-disk format,
    and sometimes embeds known fragments to exercise meaningful matching.
    ``received_at`` uses naive datetimes which round-trip exactly through
    ``isoformat``/``fromisoformat`` at microsecond resolution.
    """
    count = draw(st.integers(min_value=0, max_value=12))
    # Text alphabet excludes \n and \r (line separators) but keeps TAB and other
    # printable characters to stress the serialization format.
    alphabet = "".join(c for c in string.printable if c not in "\r\n")
    lines: list[RawLogLine] = []
    for i in range(count):
        free_text = draw(st.text(alphabet=alphabet, min_size=0, max_size=20))
        embedded = draw(st.lists(st.sampled_from(_FRAGMENTS), min_size=0, max_size=2))
        parts = [free_text, *embedded]
        draw(st.randoms()).shuffle(parts)
        text = " ".join(parts)
        received_at = draw(st.datetimes())
        lines.append(RawLogLine(text=text, received_at=received_at))
    return lines


def _read_back(path: Path) -> list[RawLogLine]:
    """Read a written export file back into a list of RawLogLine.

    Splits the file on newlines and ignores a single trailing empty entry
    produced by the trailing newline of the last record.
    """
    content = path.read_text(encoding="utf-8")
    raw_lines = content.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]
    return [_deserialize(raw) for raw in raw_lines]


# Feature: iphone-spotlight-indexing-monitor, Property 13: 导出模式正确性
# Validates: Requirements 7.2
@settings(max_examples=100)
@given(lines=_log_lines(), rules=_filter_rules(), case_sensitive=st.booleans())
def test_export_mode_correctness(
    lines: list[RawLogLine], rules: list[FilterRule], case_sensitive: bool
) -> None:
    log_filter = LogFilter(rules, case_sensitive=case_sensitive)
    writer = LogWriter(log_filter=log_filter)

    with tempfile.TemporaryDirectory() as tmp:
        raw_path = Path(tmp) / "raw.log"
        filtered_path = Path(tmp) / "filtered.log"

        # RAW: exported content equals all input lines, in order.
        writer.export(raw_path, lines, ExportMode.RAW)
        assert _read_back(raw_path) == list(lines)

        # FILTERED: exported content equals exactly the LogFilter result.
        writer.export(filtered_path, lines, ExportMode.FILTERED)
        expected = list(LogFilter(rules, case_sensitive=case_sensitive).filter_stream(lines))
        assert _read_back(filtered_path) == expected
