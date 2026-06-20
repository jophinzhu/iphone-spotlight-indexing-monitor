# spotlight-monitor

A Windows tool that reads a USB-connected iPhone's live system log (via
[libimobiledevice](https://libimobiledevice.org/)'s `idevicesyslog`), filters
Spotlight indexing-related lines, and parses/displays the indexing progress —
replacing the Mac-only Console.app workflow.

## Requirements

- Windows 10/11
- [iTunes](https://www.apple.com/itunes/) or Apple Devices app (for the USB driver)

The libimobiledevice tools and Python runtime are bundled — no separate installation needed.

## Usage

Download `spotlight-monitor.exe` from the [Releases](../../releases) page, connect
your iPhone via USB, and double-click the exe.

## Development

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
pytest
```

## Run from source

```cmd
python -m spotlight_monitor
```

## Build a Windows executable

```cmd
pip install -e .[build]
pyinstaller --onefile --name spotlight-monitor --paths src --add-data "vendor/libimobiledevice;libimobiledevice" src/spotlight_monitor/__main__.py
```
