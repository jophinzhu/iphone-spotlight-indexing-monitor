"""Unit tests for Config_Manager missing-file behavior (Task 2.6).

Example-based (plain pytest) tests verifying that when the configuration file
does not exist, :meth:`ConfigManager.load` falls back to the built-in default
rules AND generates a default configuration file at the given path (Req 5.2).

Design reference: ``design.md`` -> "Error Handling" -> "配置文件缺失".
"""

from __future__ import annotations

from pathlib import Path

from spotlight_monitor.config_manager import ConfigManager


def test_load_missing_file_returns_default_and_generates_file(tmp_path: Path) -> None:
    """Loading a non-existent config uses defaults and writes a default file (Req 5.2)."""
    path = tmp_path / "config.json"
    assert not path.exists()  # precondition: the file genuinely does not exist

    manager = ConfigManager()
    config = manager.load(path)

    # Uses the built-in default rules (Req 5.2).
    assert config == ConfigManager.default_config()

    # A default configuration file was generated at the path (Req 5.2).
    assert path.exists()

    # The normal missing-file case is not an error condition.
    assert manager.last_load_errors == []


def test_generated_default_contains_default_keywords(tmp_path: Path) -> None:
    """The generated default config carries the expected DEFAULT_KEYWORDS (Req 3.4, 5.2)."""
    path = tmp_path / "config.json"

    config = ConfigManager().load(path)

    # The single default filter rule contains the expected default keywords.
    keyword_sets = [rule.keywords for rule in config.filter_rules]
    assert ConfigManager.DEFAULT_KEYWORDS in keyword_sets


def test_second_load_reads_generated_file_roundtrip(tmp_path: Path) -> None:
    """Loading the just-written file again returns the equivalent default config (Req 5.1, 5.2)."""
    path = tmp_path / "config.json"

    manager = ConfigManager()
    first = manager.load(path)        # writes the default file
    assert path.exists()

    second = manager.load(path)       # reads the file written above

    # Round-trip sanity: the persisted file deserializes back to the default.
    assert second == first == ConfigManager.default_config()

    # Reading a valid existing file produces no errors.
    assert manager.last_load_errors == []
