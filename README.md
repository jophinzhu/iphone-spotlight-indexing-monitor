# spotlight-monitor

A Windows tool that reads a USB-connected iPhone's live system log (via
[libimobiledevice](https://libimobiledevice.org/)'s `idevicesyslog`), filters
Spotlight indexing-related lines, and parses/displays the indexing progress —
replacing the Mac-only Console.app workflow.

## Requirements

- iTunes
- Python 3.11+
- [libimobiledevice](https://libimobiledevice.org/) executables on `PATH`
  (`idevice_id`, `ideviceinfo`, `idevicesyslog`) and a working USB driver.

## Development

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
pytest
```

## Run

```cmd
python -m spotlight_monitor
```

## Build a Windows executable

```cmd
pip install -e .[build]
pyinstaller --onefile --name spotlight-monitor src\spotlight_monitor\__main__.py
```

## Project layout

```
src/spotlight_monitor/   package source
  models.py              core immutable data models and enums
  cli.py                 CLI / PyInstaller entry point (main)
  __main__.py            python -m spotlight_monitor
tests/                   unit and property-based tests
```
