"""Property-based test for cross-stage timestamp preservation (Task 11.6).

Uses Hypothesis to verify that a :class:`RawLogLine`'s ``received_at`` — assigned
once at capture time — survives unchanged as the line flows through every stage
of the pipeline: filtering (:class:`LogFilter`), parsing
(:class:`ProgressParser`), display (:class:`OutputDisplay`), disk writing
(:class:`LogWriter`) and the orchestrator's per-line processing
(:meth:`IndexingMonitor.process_line`).

Design reference: ``design.md`` -> "Correctness Properties" -> Property 7.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from spotlight_monitor.config_manager import ConfigManager
from spotlight_monitor.indexing_monitor import IndexingMonitor
from spotlight_monitor.log_filter import LogFilter
from spotlight_monitor.log_writer import LogWriter, _deserialize
from spotlight_monitor.models import RawLogLine
from spotlight_monitor.output_display import OutputDisplay
from spotlight_monitor.progress_parser import ProgressParser

# Arbitrary text for a log line. Newlines ("\n", "\r") are EXCLUDED because the
# on-disk format is one-record-per-line ("{received_at.isoformat()}\t{text}\n"),
# so a newline inside the text would break the line-based disk round-trip. TABs
# ARE allowed: LogWriter._deserialize splits on the FIRST tab, so tabs inside
# the text survive. This matches reality: the streamer rstrips newlines.
_arbitrary_text = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=0x2FFF,
        blacklist_characters="\n\r",
    ),
    min_size=0,
    max_size=60,
)

# Some texts are biased to contain filter keywords AND a progress pattern so the
# default filter keeps them and the default parser extracts progress — this
# exercises the parsing/display/write stages meaningfully (not just filtering).
_progress_text = st.builds(
    lambda pct: f"corespotlight indexing progress {pct}%",
    st.integers(min_value=0, max_value=100),
)
_keyword_text = st.sampled_from(
    ["spotlight indexing started", "mds progress update", "corespotlight ready"]
)

_line_text = st.one_of(_arbitrary_text, _keyword_text, _progress_text)

# Naive datetimes (no tzinfo) so isoformat / fromisoformat round-trip exactly at
# microsecond resolution with no timezone-offset ambiguity.
_received_at = st.datetimes()

_log_lines = st.lists(
    st.builds(RawLogLine, text=_line_text, received_at=_received_at),
    max_size=30,
)


# Feature: iphone-spotlight-indexing-monitor, Property 7: 跨阶段时间戳保持
@settings(max_examples=100)
@given(lines=_log_lines)
def test_property_7_cross_stage_timestamp_preservation(
    lines: list[RawLogLine],
) -> None:
    """A line's ``received_at`` is unchanged after filter/parse/display/disk (4.5, 7.3).

    For a generated list of :class:`RawLogLine`, the same ``received_at`` value
    assigned at capture must survive every pipeline stage. Using naive datetimes
    means the disk isoformat round-trip is exact.
    """
    config = ConfigManager.default_config()
    log_filter = LogFilter(config.filter_rules, config.case_sensitive)
    parser = ProgressParser(config.parse_rules)

    # -- Stage 1: Filtering -------------------------------------------------
    # Every surviving line is the SAME object as the matching input line, so its
    # received_at is necessarily identical (neither lost nor altered).
    survivors = list(log_filter.filter_stream(lines))
    survivor_ids = {id(line) for line in survivors}
    inputs_by_id = {id(line): line for line in lines}
    for survived in survivors:
        assert id(survived) in inputs_by_id
        assert survived.received_at == inputs_by_id[id(survived)].received_at
    # The surviving set must be a subset of the inputs (sanity on filtering).
    assert survivor_ids.issubset(set(inputs_by_id))

    # -- Stage 2: Parsing ---------------------------------------------------
    # When parse returns progress, observed_at must equal the line's received_at.
    for line in lines:
        progress = parser.parse(line)
        if progress is not None:
            assert progress.observed_at == line.received_at

    # -- Stage 3: Display ---------------------------------------------------
    # show_log_line must render the line's timestamp verbatim (not altered).
    for line in lines:
        stream = io.StringIO()
        OutputDisplay(stream=stream).show_log_line(line)
        rendered = stream.getvalue()
        assert line.received_at.isoformat() in rendered

    # -- Stage 4: Disk writing ----------------------------------------------
    # Write all lines, read them back, and confirm received_at (and order) hold.
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

    raw_records = content.split("\n")
    if raw_records and raw_records[-1] == "":
        raw_records = raw_records[:-1]
    recovered = [_deserialize(record) for record in raw_records]

    assert len(recovered) == len(lines)
    for original, parsed in zip(lines, recovered):
        assert parsed.received_at == original.received_at

    # -- Stage 5 (optional): orchestrator process_line ----------------------
    # When the monitor's combined filter+parse yields progress, observed_at must
    # still equal the original received_at.
    monitor = IndexingMonitor(config=config)
    for line in lines:
        progress = monitor.process_line(line)
        if progress is not None:
            assert progress.observed_at == line.received_at
