"""Configuration management for the Spotlight indexing monitor (Req 5).

This module owns the user-configurable filter and parse rules. It provides the
built-in default configuration and validation of regex patterns. Loading and
saving (JSON serialization) are added in a later task; this file is structured
so that ``load`` / ``save`` can be slotted in alongside the existing helpers
without disturbing the public surface.

Design reference: ``design.md`` -> "Config_Manager".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import AppConfig, FilterRule, ParseRule

__all__ = ["ConfigError", "ConfigManager"]


class ConfigError(Exception):
    """Raised when configuration handling fails irrecoverably."""


class ConfigManager:
    """Load, save and validate filter/parse rules (Req 5)."""

    # Default Spotlight-indexing keywords, matched case-insensitively (Req 3.4).
    DEFAULT_KEYWORDS: tuple[str, ...] = (
        "PipelineCompleteness",
    )

    def __init__(self) -> None:
        # Side channel for surfacing non-fatal load errors without raising.
        # Populated by ``load`` on every call (cleared at the start of each
        # call). Consumers (e.g. Output_Display) read this to report problems
        # while still receiving a usable configuration (Req 5.3).
        self.last_load_errors: list[str] = []

    @staticmethod
    def default_config() -> AppConfig:
        """Return the built-in default configuration (Req 3.4, 5.2).

        Includes a single default keyword filter rule covering the Spotlight
        indexing keywords, plus two generic progress parse rules: a percentage
        form (``progress ... NN%``) normalized against a scale of 100, and a
        fraction form (``N / M items``) normalized against a scale of 1.0.
        """
        filter_rules = (
            FilterRule(
                id="default-keywords",
                keywords=ConfigManager.DEFAULT_KEYWORDS,
                pattern=None,
                enabled=True,
            ),
        )
        parse_rules = (
            ParseRule(
                id="pipeline-completeness",
                pattern=r"PipelineCompleteness:\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%",
                scale_max=100.0,
            ),
            ParseRule(
                id="percent-generic",
                pattern=r"progress[^0-9]*([0-9]{1,3}(?:\.[0-9]+)?)\s*%",
                scale_max=100.0,
            ),
            ParseRule(
                id="fraction-generic",
                pattern=r"([0-9]+)\s*/\s*([0-9]+)\s*items",
                scale_max=1.0,
            ),
        )
        return AppConfig(
            filter_rules=filter_rules,
            parse_rules=parse_rules,
            case_sensitive=False,
        )

    @staticmethod
    def validate(config: AppConfig) -> list[str]:
        """Validate a configuration, returning a list of error messages.

        An empty list means the configuration is valid. Every
        ``FilterRule.pattern`` (when present) and every ``ParseRule.pattern``
        must compile successfully via :func:`re.compile` (Req 5.4, 5.5).
        """
        errors: list[str] = []

        for rule in config.filter_rules:
            if rule.pattern is not None:
                error = ConfigManager._compile_error(rule.pattern)
                if error is not None:
                    errors.append(
                        f"FilterRule '{rule.id}' has an invalid pattern: {error}"
                    )

        for rule in config.parse_rules:
            error = ConfigManager._compile_error(rule.pattern)
            if error is not None:
                errors.append(
                    f"ParseRule '{rule.id}' has an invalid pattern: {error}"
                )

        return errors

    @staticmethod
    def _compile_error(pattern: str) -> str | None:
        """Return the error message if ``pattern`` fails to compile, else None."""
        try:
            re.compile(pattern)
        except re.error as exc:
            return str(exc)
        return None

    # ------------------------------------------------------------------
    # Persistence (Req 5.1, 5.2, 5.3, 5.4)
    # ------------------------------------------------------------------

    def save(self, path: Path, config: AppConfig) -> None:
        """Validate ``config`` and persist it as JSON to ``path`` (Req 5.4).

        The configuration is validated first; if any errors are found (e.g. an
        uncompilable regex, Req 5.5) a :class:`ConfigError` is raised listing
        them and nothing is written. Otherwise the configuration is serialized
        to JSON and written to ``path``, creating parent directories as needed.
        """
        errors = self.validate(config)
        if errors:
            raise ConfigError(
                "Cannot save invalid configuration: " + "; ".join(errors)
            )

        path = Path(path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        payload = self._serialize(config)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load(self, path: Path) -> AppConfig:
        """Load configuration from ``path`` (Req 5.1, 5.2, 5.3).

        Behavior:

        * If the file does not exist, the built-in default configuration is
          written to ``path`` and returned (Req 5.2).
        * If the file exists but is invalid (bad JSON, missing/extra/wrong-typed
          fields, or an uncompilable regex), no uncaught exception is raised:
          the built-in default configuration is returned and the error
          message(s) are recorded on :attr:`last_load_errors` (Req 5.3).

        Errors are reported via the :attr:`last_load_errors` side channel rather
        than by raising, so callers always receive a usable configuration.
        """
        self.last_load_errors = []
        path = Path(path)

        if not path.exists():
            default = self.default_config()
            try:
                self.save(path, default)
            except (OSError, ConfigError) as exc:
                # Generating the default file is best-effort; failing to write
                # it must not prevent returning usable defaults (Req 5.2).
                self.last_load_errors.append(
                    f"Could not write default configuration to '{path}': {exc}"
                )
            return default

        try:
            text = path.read_text(encoding="utf-8")
            raw = json.loads(text)
            config = self._deserialize(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.last_load_errors.append(
                f"Invalid configuration in '{path}': {exc}"
            )
            return self.default_config()

        errors = self.validate(config)
        if errors:
            self.last_load_errors.extend(errors)
            return self.default_config()

        return config

    # ------------------------------------------------------------------
    # JSON (de)serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(config: AppConfig) -> dict[str, Any]:
        """Convert an :class:`AppConfig` into a JSON-serializable dict."""
        return {
            "case_sensitive": config.case_sensitive,
            "filter_rules": [
                {
                    "id": rule.id,
                    "keywords": list(rule.keywords),
                    "pattern": rule.pattern,
                    "enabled": rule.enabled,
                }
                for rule in config.filter_rules
            ],
            "parse_rules": [
                {
                    "id": rule.id,
                    "pattern": rule.pattern,
                    "scale_max": rule.scale_max,
                }
                for rule in config.parse_rules
            ],
        }

    @staticmethod
    def _deserialize(raw: Any) -> AppConfig:
        """Build an :class:`AppConfig` from parsed JSON, validating its shape.

        Raises :class:`TypeError`, :class:`KeyError` or :class:`ValueError` if
        the structure or field types are invalid (including missing or extra
        fields). Callers convert these into a fallback-to-default outcome.
        """
        if not isinstance(raw, dict):
            raise TypeError("configuration root must be a JSON object")

        expected_top = {"case_sensitive", "filter_rules", "parse_rules"}
        actual_top = set(raw.keys())
        if actual_top != expected_top:
            raise ValueError(
                f"unexpected top-level fields: expected {sorted(expected_top)}, "
                f"got {sorted(actual_top)}"
            )

        case_sensitive = raw["case_sensitive"]
        if not isinstance(case_sensitive, bool):
            raise TypeError("'case_sensitive' must be a boolean")

        filter_rules_raw = raw["filter_rules"]
        if not isinstance(filter_rules_raw, list):
            raise TypeError("'filter_rules' must be a list")
        filter_rules = tuple(
            ConfigManager._deserialize_filter_rule(item) for item in filter_rules_raw
        )

        parse_rules_raw = raw["parse_rules"]
        if not isinstance(parse_rules_raw, list):
            raise TypeError("'parse_rules' must be a list")
        parse_rules = tuple(
            ConfigManager._deserialize_parse_rule(item) for item in parse_rules_raw
        )

        return AppConfig(
            filter_rules=filter_rules,
            parse_rules=parse_rules,
            case_sensitive=case_sensitive,
        )

    @staticmethod
    def _deserialize_filter_rule(item: Any) -> FilterRule:
        if not isinstance(item, dict):
            raise TypeError("each filter rule must be a JSON object")

        expected = {"id", "keywords", "pattern", "enabled"}
        if set(item.keys()) != expected:
            raise ValueError(
                f"filter rule has unexpected fields: expected {sorted(expected)}, "
                f"got {sorted(item.keys())}"
            )

        rule_id = item["id"]
        if not isinstance(rule_id, str):
            raise TypeError("filter rule 'id' must be a string")

        keywords_raw = item["keywords"]
        if not isinstance(keywords_raw, list) or not all(
            isinstance(kw, str) for kw in keywords_raw
        ):
            raise TypeError("filter rule 'keywords' must be a list of strings")

        pattern = item["pattern"]
        if pattern is not None and not isinstance(pattern, str):
            raise TypeError("filter rule 'pattern' must be a string or null")

        enabled = item["enabled"]
        if not isinstance(enabled, bool):
            raise TypeError("filter rule 'enabled' must be a boolean")

        return FilterRule(
            id=rule_id,
            keywords=tuple(keywords_raw),
            pattern=pattern,
            enabled=enabled,
        )

    @staticmethod
    def _deserialize_parse_rule(item: Any) -> ParseRule:
        if not isinstance(item, dict):
            raise TypeError("each parse rule must be a JSON object")

        expected = {"id", "pattern", "scale_max"}
        if set(item.keys()) != expected:
            raise ValueError(
                f"parse rule has unexpected fields: expected {sorted(expected)}, "
                f"got {sorted(item.keys())}"
            )

        rule_id = item["id"]
        if not isinstance(rule_id, str):
            raise TypeError("parse rule 'id' must be a string")

        pattern = item["pattern"]
        if not isinstance(pattern, str):
            raise TypeError("parse rule 'pattern' must be a string")

        scale_max = item["scale_max"]
        # bool is a subclass of int; reject it explicitly.
        if isinstance(scale_max, bool) or not isinstance(scale_max, (int, float)):
            raise TypeError("parse rule 'scale_max' must be a number")

        return ParseRule(
            id=rule_id,
            pattern=pattern,
            scale_max=float(scale_max),
        )
