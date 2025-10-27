# SWE Productivity Recorder

A macOS screen activity recorder for built on top of gum. It guides a participant through selecting the windows they are comfortable sharing, records high-signal screen activity around user interactions, and stores the resulting timeline in a searchable SQLite database.

The project pairs a command-line facilitator (`cli.py`) with an asynchronous observer framework (`gum.py`) and a rich `Screen` observer that captures before/after screenshots, keyboard sessions, scroll events, and inactivity timers.

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

Install them into a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
