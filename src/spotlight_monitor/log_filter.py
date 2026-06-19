"""Log_Filter (pure logic): keyword/regex matching and streaming filter.

Decides whether a log line matches any enabled :class:`FilterRule` and produces
an order-preserving subset of matching lines. This component has no side
effects, which makes it directly amenable to property-based testing.

Design reference: ``design.md`` -> "Log_Filter（纯逻辑）".
Requirements: 3.1, 3.2, 3.3, 3.5.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence

from .models import FilterRule, RawLogLine

__all__ = ["LogFilter"]


class LogFilter:
    """Filters log lines against a set of :class:`FilterRule` objects.

    A line matches a rule when ANY of the rule's keywords is a substring of the
    line, OR the rule's optional regex ``pattern`` matches. Only enabled rules
    are considered. The ``case_sensitive`` flag controls both keyword and regex
    matching (regexes are compiled with ``re.IGNORECASE`` when not
    case-sensitive).
    """

    def __init__(self, rules: Sequence[FilterRule], case_sensitive: bool = False) -> None:
        self._rules: tuple[FilterRule, ...] = tuple(rules)
        self._case_sensitive = case_sensitive

        # Pre-compile patterns of enabled rules for efficiency. Invalid patterns
        # are skipped (treated as non-matching) so a single bad rule never breaks
        # the whole filter; pattern validity is enforced by Config_Manager.
        flags = 0 if case_sensitive else re.IGNORECASE
        self._compiled: dict[str, re.Pattern[str] | None] = {}
        for rule in self._rules:
            if not rule.enabled or rule.pattern is None:
                continue
            try:
                self._compiled[rule.id] = re.compile(rule.pattern, flags)
            except re.error:
                self._compiled[rule.id] = None

    @property
    def case_sensitive(self) -> bool:
        return self._case_sensitive

    def matches(self, line: str) -> bool:
        """Return True when ``line`` matches any enabled rule (3.1, 3.2, 3.5)."""
        haystack = line if self._case_sensitive else line.lower()

        for rule in self._rules:
            if not rule.enabled:
                continue

            for keyword in rule.keywords:
                needle = keyword if self._case_sensitive else keyword.lower()
                if needle in haystack:
                    return True

            if rule.pattern is not None:
                compiled = self._compiled.get(rule.id)
                if compiled is not None and compiled.search(line) is not None:
                    return True

        return False

    def filter_stream(self, lines: Iterable[RawLogLine]) -> Iterator[RawLogLine]:
        """Yield only matching lines, preserving arrival order (3.3).

        Non-matching lines are dropped. ``received_at`` is left unchanged on the
        yielded lines.
        """
        for line in lines:
            if self.matches(line.text):
                yield line
