# Rail Operational Intelligence — v3.0.0 | Overview & Reporting Hub

**Python · SQLite · CustomTkinter · Jupyter · Playwright (optional) · PDF/HTML reporting**

A local-first desktop operational intelligence demonstration. **Release v3.0.0** introduces an executive Overview, workforce and safety monitoring, a separate Exceptions workflow for staffing changes before/after 15:00, manual/Excel staffing plans, and a searchable local Reports hub. Uses synthetic examples and no hosted database or external APIs; WhatsApp Web collection is optional and requires an authorized browser session.

> **Publication warning:** The underlying application originated from an employer-connected workflow. Confirm you have permission to publish the **code itself**. Data in `examples/` is synthetic; no live records, browser sessions, credentials or actual employee information belong in a public repository. See [SECURITY.md](SECURITY.md).

## What changed since the first v2.8 package?

See [CHANGELOG.md](CHANGELOG.md), [v3.0.0 release notes](RELEASE_NOTES_v3.0.0.md) and the [v2.8 → v3.0.0 application comparison](docs/MIGRATION_v2.8_to_v3.0.0.md).

- New Overview and Reports hub, searchable file catalogue and collapsible navigation.
- Changes before/after 15:00 and exception tracking with revision checks and audit history.
- Date-effective staffing plans from read-only Excel tracker imports or manual entry.
- Local administrator/operator users, hashed passwords, recovery codes and login sessions.
- Expanded SQLite schema and exception-aware PDF / standalone HTML exports.
- Existing WhatsApp collector and CSV/Excel formats preserved, with blank template rows ignored.

## Repository layout

```text
├── app.py                    Desktop launcher
├── core.py                   Data/analytics, staffing exceptions, user accounts and report engines
├── collector.py              Optional authorized WhatsApp Web collector
├── desktop.py                Overview/Exceptions/Reports GUI
├── manage.py                 SQLite and ingestion CLI
├── requirements.txt          Application dependencies
├── requirements-dev.txt      Test tools
├── CHANGELOG.md              Release history and change summary
├── RELEASE_NOTES_v3.0.0.md  Release description for GitHub
├── SECURITY.md               Privacy and local-security guidance
├── notebooks/
│   ├── Operational_Intelligence_v3.0.0.ipynb       Current application notebook (clean outputs)
│   └── Operational_Intelligence_Demo.ipynb      Synthetic analytics walkthrough
├── templates/                Original supplied CSV and Excel field layouts
├── examples/                 168 synthetic reports, 2 synthetic plans, 8 exceptions (SQLite/CSV/Excel)
├── docs/                     Architecture, data dictionary, schema, migration and GitHub guide
├── tests/                    Headless regression tests
├── .github/workflows/         Automated GitHub Actions tests
└── data/.gitkeep              Private local runtime folder (never upload its contents)
```

## Run on Windows (VS Code, PowerShell)

From the extracted root folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py init
.\.venv\Scripts\python.exe manage.py status
.\.venv\Scripts\python.exe app.py
```

Python 3.10–3.12 is the supported project target; a GUI environment is required for the dashboard.

To use synthetic data rather than a blank database, open the application and choose its synthetic-data option or run the CSV/Excel import command on a **new empty local database**:

```powershell
.\.venv\Scripts\python.exe manage.py import --csv examples\demo_WhatsAppData.csv --excel examples\demo_WhatsAppData.xlsx
```

Both files contain matching synthetic records; deduplication should result in **168 inserted, 168 duplicates skipped**, for an empty database.

### Exploring the notebooks

- `notebooks/Operational_Intelligence_v3.0.0.ipynb` is the source version of the standalone application: execute its cells **in order**, with the working directory set to the repository root, in a properly configured local Python environment.
- `notebooks/Operational_Intelligence_Demo.ipynb` is a small read-only analytics walkthrough using the packaged synthetic database.
- The normal application launch is via `app.py`, which avoids needing to execute every notebook cell manually.

## Existing v2.8 database migration

Release v3.0.0 adds a nullable `no_shows` field to historical messages and creates additional exception, plan and account tables. **Back up an existing live `data/operations.db` outside the Git repository before running the new code.** The migration is one-way for this package; reverting to the old application version is not tested.

On a new local install, `manage.py init` creates both the database and working copies of the input templates. Do not overwrite your actual local CSV/Excel sources with the synthetic files.

## Optional collector

For authorized local WhatsApp Web tests only:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe app.py
```

Use the GUI Start/Stop controls and sign in to WhatsApp Web if prompted. This is browser UI collection, **not** the official WhatsApp API; UI selectors and access terms can change. Never include `whatsapp_session/` in Git.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The automated suite covers template inputs, date semantics, deduplication, no-show null handling, expanded database schema, staffing plans, before/after-15:00 workflows, account creation/recovery and local database integrity. Live GUI/authenticated WhatsApp testing is manual, and actual employer data has not been used.

## Documentation and updates

- [System architecture](docs/architecture.md)
- [Database and field definitions](docs/data_dictionary.md)
- [SQLite reference schema](docs/schema.sql)
- [Comparison with original v2.8](docs/MIGRATION_v2.8_to_v3.0.0.md)
- [How to update your existing GitHub repository](docs/GITHUB_UPDATE_GUIDE.md)
- [Security guidance](SECURITY.md)

**Release status:** v3.0.0 GitHub package with the current application notebook and synthetic example data; GUI and WhatsApp connectivity require real desktop validation before a production claim. This repository intentionally has no license until code ownership and permission are verified.
