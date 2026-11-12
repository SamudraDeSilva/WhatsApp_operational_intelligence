# Architecture — v3.0.0

## Application layers

```text
Authorized WhatsApp Web browser session (optional)
    │  Playwright collector / message parser
    ▼
CSV staging + Excel workbook (11 original columns)
    │  ingestion and duplicate checks
    ▼
Local SQLite database  ──── ◀ Excel planned staffing tracker (read-only import)
    │                       ◀ Manual staffing plan versions
    ├── reports / safety metrics / alerts
    ├── staffing plans / exception entries / audit log
    ├── locally hashed user accounts / local sessions
    ▼
CustomTkinter dashboard (Overview, Workforce, Executive, Exceptions, Reports)
    ├── PDF and interactive HTML snapshots
    ├── CSV and Excel exports
    └── Local report catalogue in data/reports/
```

## Code modules

- `app.py`: graphical entry point.
- `core.py`: SQLite, CSV/XLSX imports, analytics, staffing exceptions, user management and PDF/HTML exports; the notebook's core and exceptions cells are in the same module to keep internal references coherent.
- `collector.py`: optional WhatsApp Web collector and bridge into `core.py`.
- `desktop.py`: CustomTkinter interface.
- `manage.py`: headless initialization, importing, database diagnostics, synthetic seed.
- `notebooks/`: fully sourced v3.0.0 application notebook and separate demo notebook.

## Operational safeguards

- CSV/Excel staging and the optional browser collector run only with locally stored files or authorized browser sessions.
- Unknown values are stored as NULL, not guessed as zeros.
- Local database, configuration files, credentials, browser profile and report catalogues are excluded from Git.
- The Excel staffing plan import reads visible worksheet data only; ambiguous overlapping plans must be resolved manually.
- This is a local desktop application, not a hosted web service. A session or local password does not secure an untrusted shared computer.
