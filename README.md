# SWE Productivity Recorder

A screen activity recorder built on top of [gum](https://github.com/GeneralUserModels/gum). It guides a participant through selecting the windows they are comfortable sharing, records high-signal screen activity around user interactions, and stores the resulting timeline in a searchable SQLite database.

The project pairs a command-line facilitator (`cli.py`) with an asynchronous observer framework (`gum.py`) and a `Screen` observer that captures before/after screenshots, keyboard sessions, and mouse events.

## Architecture

- `cli.py` – argument parsing, participant briefing, and lifecycle orchestration.
- `gum.py` – async context manager that fans in updates from one or more observers and stores them as `observation` rows.
- `observers/` – concrete observer implementations. `screen` handles region selection, screenshot capture, scroll tracking, keyboard sessions, and inactivity detection.
- `models.py` – SQLAlchemy ORM + FTS5 schema for observations and derived propositions, plus async engine/session helpers.
- `db_utils.py` – helper queries (BM25 + MMR ranking, related observation lookups) for analysis workflows.
- `schemas.py` – pydantic models describing the JSON update payloads and LLM-facing schemas.

## Requirements

- macOS 12 or later
- System permissions:
  - Screen Recording permission for your terminal
  - Accessibility permission for keyboard/mouse monitoring
  - Grant these in: System Settings → Privacy & Security
- Python 3.11 (3.10+ should work, but 3.11 is what the type hints target).
- Homebrew-installed `sqlite`/`libsqlite3` is recommended for the bundled FTS5 support.
- Python packages:
  - `python-dotenv`, `SQLAlchemy[asyncio]`, `aiosqlite`, `pydantic`
  - `numpy`, `scikit-learn` (for post-processing search utilities)
  - `mss`, `Pillow`, `shapely`, `pynput`
  - `pyobjc-framework-Quartz`, `pyobjc-framework-AppKit`, `pyobjc-core` (macOS automation APIs)
  - `PyDrive` if you plan to upload to Google Drive.

### Installation

Install them into a virtual environment with [uv](https://docs.astral.sh/uv/#installation) venv **(recommended)**:

```bash
uv venv
source .venv/bin/activate
uv sync
```

or using a Python standard library venv virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pyproject.toml
```

### Google Drive upload (optional)

To additionally install the `gdrive` extra with uv:

```bash
uv sync --extra gdrive
```

or with pip:

```bash
pip install -e ".[gdrive]"
```

## Usage

Run the recorder with:
```bash
swe-prod-recorder [OPTIONS]
```

```
usage: swe-prod-recorder [-h] [--user-name USER_NAME] [--debug]
                         [--scroll-debounce SCROLL_DEBOUNCE]
                         [--scroll-min-distance SCROLL_MIN_DISTANCE]
                         [--scroll-max-frequency SCROLL_MAX_FREQUENCY]
                         [--scroll-session-timeout SCROLL_SESSION_TIMEOUT]
                         [--upload-to-gdrive]
                         [--screenshots-dir SCREENSHOTS_DIR]
                         [--inactivity-timeout INACTIVITY_TIMEOUT]

SWE Productivity Recorder - Screen activity recorder for software engineer
productivity research

options:
  -h, --help            show this help message and exit
  --user-name, -u USER_NAME
                        The user name to use
  --debug, -d           Enable debug mode
  --scroll-debounce SCROLL_DEBOUNCE
                        Minimum time between scroll events (seconds, default:
                        0.5)
  --scroll-min-distance SCROLL_MIN_DISTANCE
                        Minimum scroll distance to log (pixels, default: 5.0)
  --scroll-max-frequency SCROLL_MAX_FREQUENCY
                        Maximum scroll events per second (default: 10)
  --scroll-session-timeout SCROLL_SESSION_TIMEOUT
                        Scroll session timeout (seconds, default: 2.0)
  --upload-to-gdrive    Upload screenshots to Google Drive (default: keep
                        local)
  --screenshots-dir SCREENSHOTS_DIR
                        Directory to save screenshots (default:
                        data/screenshots)
  --inactivity-timeout INACTIVITY_TIMEOUT
                        Stop recording after N minutes of inactivity (default:
                        45)
```

## Running a Recording Session

1. Ensure you are in the repository root (the directory with `cli.py`).
2. Export any environment variables needed by your study (they can also live in a `.env` file that `python-dotenv` will pick up).
3. Launch the recorder:

```bash
python -m recorder --user-name alice
```

4. Read the on-screen safety reminders, press Enter to continue, then use the overlay to select one or more windows/regions.
5. The recorder runs until you press `Ctrl+C` or it detects the configured inactivity timeout. Data lands in `data/actions.db`, and screenshots default to `data/screenshots/`.

### CLI Options

- `--user-name/-u` – Tag all observations with a participant identifier (default: `anonymous`).
- `--debug/-d` – Verbose logging and extra console diagnostics.
- `--screenshots-dir` – Target folder for captured PNGs (default: `data/screenshots`).
- `--upload-to-gdrive` – Upload screenshots to Drive instead of keeping them locally. Requires a `client_secrets.json` and consent flow (see below).
- Scroll filtering knobs: `--scroll-debounce`, `--scroll-min-distance`, `--scroll-max-frequency`, `--scroll-session-timeout`.
- `--inactivity-timeout` – Minutes of inactivity before auto-stop (default: 45).

Run `python -m recorder -h` to see the full help text.

## Data Layout

- `data/actions.db` – SQLite database containing the `observations` table and derived `propositions` tables for higher-level analytics. WAL mode is enabled for concurrent reads.
- `data/screenshots/` – Timestamped PNGs capturing pre/post interaction frames. Files are pruned when keyboard sessions finish or when the hour-long retention window elapses.
- Optional Google Drive uploads mirror the screenshot filenames in the chosen folder and remove the local copies once the upload succeeds.

You can inspect the database with:

```bash
sqlite3 data/actions.db '.tables'
```

`db_utils.py` provides convenience functions for BM25 search (`search_propositions_bm25`) and fetching related observations; import them into your analysis notebooks as needed.

## Google Drive Uploads

1. Create OAuth credentials in Google Cloud Console and download `client_secrets.json`.
2. Pass its path via `--upload-to-gdrive` (and optionally tweak `client_secrets_path` inside the observer) so PyDrive can authenticate.
3. The first run launches a browser for consent. After that, cached credentials let subsequent runs upload silently.

Uploads target the folder ID you configure in your integration logic. Review and purge Drive artifacts regularly to honor participant agreements.

## Project Structure

```
recorder/
├── cli.py                # Command-line entry point
├── gum.py                # Observer manager + database writer
├── models.py             # SQLAlchemy ORM models and engine helpers
├── db_utils.py           # Search utilities for captured propositions
├── observers/
│   ├── screen.py         # Screenshot + activity observer
│   ├── window.py         # macOS window selection overlay
│   └── observer.py       # Abstract base class for observers
├── schemas.py            # pydantic schemas shared across components
└── __main__.py           # Allows `python -m recorder` execution
```

## Attribution

This project is built on top of [GUM (General User Models)](https://github.com/GeneralUserModels/gum)
(MIT License) by Omar Shaikh.
The core observer pattern and database architecture are adapted from that project.

The Linux window manager and graphics integration were vendored from [pyx-sys](https://github.com/lmmx/pyx-sys)
(MIT License) by Louis Maddox.
