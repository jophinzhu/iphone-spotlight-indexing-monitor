"""Property-based test for Log_Writer disk write round-trip (Task 6.3).

Uses Hypothesis to verify that a sequence of :class:`RawLogLine` written to a
file via :class:`LogWriter` can be read back so that each line's text and
``received_at`` timestamp are preserved, with order unchanged.

Design reference: ``design.md`` -> "Correctness Properties" -> Property 14.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.log_writer import LogWriter, _deserialize
from spotlight_monitor.models import RawLogLine

# Text for a log line. Newlines ("\n", "\r") are EXCLUDED because the on-disk
# format is one-record-per-line ("{received_at.isoformat()}\t{text}\n"); a
# newline inside the text would break the line-based round-trip. This mirrors
# reality: the streamer rstrips newlines, so real log lines never contain them.
# TABs ARE allowed: LogWriter._deserialize splits on the FIRST tab, so any tabs
# inside the text survive the round-trip — including them exercises that path.
_line_text = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=0x2FFF,
        blacklist_characters="\n\r",
    ),
    min_size=0,
    max_size=60,
).flatmap(
    # Occasionally splice in a TAB to exercise the "split on first tab" logic.
    lambda s: st.sampled_from([s, f"col1\t{s}", f"{s}\tcol2"])
)

# Naive datetimes (no tzinfo) so isoformat / fromisoformat round-trip exactly at
# microsecond resolution without any timezone-offset ambiguity.
_received_at = st.datetimes()

_log_lines = st.lists(
    st.builds(RawLogLine, text=_line_text, received_at=_received_at),
    max_size=30,
)


# Feature: iphone-spotlight-indexing-monitor, Property 14: 写盘往返
@settings(max_examples=100)
@given(lines=_log_lines)
def test_property_14_disk_write_roundtrip(lines: list[RawLogLine]) -> None:
    """Writing then reading back preserves each line's text, received_at and order.

    Each line is written via ``LogWriter.write`` (after ``open``), then the file
    is read back, split on "\\n" with the trailing empty entry dropped, and each
    record deserialized. The recovered list must equal the input exactly — text,
    timestamp and order (Req 7.1, 7.3). ``RawLogLine`` is a frozen dataclass, so
    ``==`` compares all fields.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "captured.log"

        writer = LogWriter()
        writer.open(path)
        try:
            for line in lines:
                writer.write(line)
        finally:
            writer.close()

        content = path.read_text(encoding="utf-8")

    # One record per line, newline-terminated. Splitting on "\n" yields a
    # trailing empty entry after the final terminator; drop it before parsing.
    raw_records = content.split("\n")
    if raw_records and raw_records[-1] == "":
        raw_records = raw_records[:-1]

    recovered = [_deserialize(record) for record in raw_records]

    assert recovered == lines
    # Sanity: a non-empty input must produce naive datetimes that survived the
    # isoformat round-trip exactly (guards against silent tz coercion).
    for original, parsed in zip(lines, recovered):
        assert isinstance(parsed.received_at, datetime)
        assert parsed.received_at.tzinfo == original.received_at.tzinfo
