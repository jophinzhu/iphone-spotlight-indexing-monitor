"""Progress_Parser: extract and normalize indexing progress from log lines.

This is a **pure-logic** component (no side effects) so it can be exercised by
property tests and used safely with rule hot-reloading. It tries each
configured :class:`~spotlight_monitor.models.ParseRule` against a log line and,
on the first match, extracts a numeric value and normalizes it into the closed
interval ``[0, 100]``.

Parsing is **best-effort / non-fatal**: when no rule matches (or the captured
text is not a number) :meth:`ProgressParser.parse` returns ``None`` rather than
raising. This is the key design decision for coping with the uncertain log
format of iOS 27 beta 1 — a line we cannot parse must never interrupt the log
stream.

Design reference: ``design.md`` -> "Progress_Parser（纯逻辑）".

Requirements: 4.1, 4.2, 4.3.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import IndexingProgress, ParseRule, RawLogLine

__all__ = ["ProgressParser"]


class ProgressParser:
    """Extract normalized :class:`IndexingProgress` from log lines (Req 4)."""

    def __init__(self, rules: Sequence[ParseRule]) -> None:
        """Store the parse rules and pre-compile their regex patterns.

        Patterns that fail to compile are skipped gracefully (parsing is
        best-effort). Compiled patterns are cached alongside their originating
        rule so :meth:`parse` can try them in order.
        """
        self._rules: tuple[ParseRule, ...] = tuple(rules)
        self._compiled: list[tuple[ParseRule, re.Pattern[str]]] = []
        for rule in self._rules:
            try:
                self._compiled.append((rule, re.compile(rule.pattern)))
            except re.error:
                # A non-compilable rule is ignored; validation of patterns is
                # the responsibility of Config_Manager (Req 5.5).
                continue

    def parse(self, line: RawLogLine) -> IndexingProgress | None:
        """Try to extract progress from ``line``; return ``None`` if none (4.1, 4.2).

        Each rule's regex is tried in order against ``line.text``. On the first
        match the value is extracted and normalized to ``[0, 100]``:

        - If the matched pattern produced **two** numeric capture groups, they
          are interpreted as ``numerator / denominator`` (a fraction such as
          ``"42 / 100 items"``). The ratio is normalized with ``scale_max``
          treated as ``1.0`` (a fraction is already in ``[0, 1]``).
        - Otherwise the **first** capture group is taken as the value and
          normalized using the rule's own ``scale_max``.

        Returns ``None`` (non-fatal) when no rule matches or when a captured
        value cannot be parsed as a number.
        """
        for rule, pattern in self._compiled:
            match = pattern.search(line.text)
            if match is None:
                continue

            groups = match.groups()
            if not groups:
                # Rule matched but captured nothing usable; treat as no match
                # and keep trying subsequent rules.
                continue

            percent = self._value_from_groups(groups, rule.scale_max)
            if percent is None:
                # Captured text was not numeric (or denominator was zero/invalid);
                # non-fatal, try the next rule.
                continue

            return IndexingProgress(
                percent=percent,
                source_line=line.text,
                observed_at=line.received_at,
            )

        return None

    @staticmethod
    def _value_from_groups(
        groups: tuple[str | None, ...], scale_max: float
    ) -> float | None:
        """Compute a normalized percent from regex capture groups.

        Handles both single-group (value + ``scale_max``) and multi-group
        (numerator/denominator fraction) rules. Returns ``None`` when the
        captured text is non-numeric or the fraction is undefined.
        """
        # Collect the non-empty groups in order; a fraction-style rule yields
        # two numeric groups (numerator, denominator).
        present = [g for g in groups if g is not None]
        if len(present) >= 2:
            try:
                numerator = float(present[0])
                denominator = float(present[1])
            except (TypeError, ValueError):
                return None
            if denominator == 0:
                return None
            ratio = numerator / denominator
            # A ratio is already expressed against its own denominator, so the
            # effective scale maximum is 1.0.
            return ProgressParser.normalize(ratio, 1.0)

        try:
            value = float(present[0])
        except (TypeError, ValueError):
            return None
        return ProgressParser.normalize(value, scale_max)

    @staticmethod
    def normalize(value: float, scale_max: float) -> float:
        """Normalize ``value`` in ``[0, scale_max]`` and clamp to ``[0, 100]`` (4.2).

        The result is ALWAYS within the closed interval ``[0, 100]``. When
        ``scale_max <= 0`` the ratio is undefined (division by zero / negative
        scale), so the value is clamped directly: any positive value maps to
        ``100`` and any non-positive value maps to ``0``.
        """
        if scale_max <= 0:
            # Undefined scale: clamp gracefully instead of dividing by zero.
            return 100.0 if value > 0 else 0.0

        percent = value / scale_max * 100.0
        # Clamp to the closed interval [0, 100].
        if percent < 0.0:
            return 0.0
        if percent > 100.0:
            return 100.0
        return percent
