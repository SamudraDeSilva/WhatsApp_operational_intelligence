# Operational Intelligence — Local Executive Reporting

**Python · SQLite · Jupyter Notebook · CustomTkinter · Optional WhatsApp Web monitoring**

A portfolio-ready, local-first demonstration of operational reporting: message
normalisation, data quality validation, idempotent CSV/Excel ingestion, SQLite,
workforce and safety KPI summaries, alert tracking, desktop visualisations and
exportable reports. **No live APIs or externally hosted database are required.**

> **Publication notice:** The supplied input files were empty templates. Every
> record in the committed demo database is synthetic. Before making this code
> public, confirm the code owner/employer permits its release; review `SECURITY.md`.
> No licence has been granted by this repository package: choose a licence only
> after confirming the IP owner and publication rights.

## Project layout

```text
Operational_Intelligence_GitHub_Ready/
├── README.md                     ← overview and setup
├── core.py                       ← SQLite, imports, metrics, analytics, exports
├── collector.py                  ← optional browser collector and CSV/Excel bridge
├── desktop.py                    ← existing interactive executive GUI
├── app.py                        ← launch dashboard
├── manage.py                     ← headless CLI: init, import, status, make-demo
├── notebooks/
│   └── Operational_Intelligence_Demo.ipynb
├── templates/
│   ├── WhatsAppData.xlsx         ← exact supplied empty Excel schema
│   └── WhatsAppData_staging.csv  ← supplied empty CSV schema, blank rows trimmed
├── examples/
│   ├── demo_operations.db        ← 100% synthetic sample SQLite database
│   ├── demo_WhatsAppData.csv     ← synthetic sample rows, same 11 columns
│   └── demo_WhatsAppData.xlsx    ← synthetic sample workbook, same 11 columns
├── docs/
│   ├── architecture.md
│   └── data_dictionary.md
├── tests/                         ← import/validation/schema/date smoke tests
└── data/                          ← local, private runtime files (gitignored)
```

## Quick start

Use **Python 3.10–3.12**. Windows is recommended for the desktop interface;
Linux/macOS require a working Tk installation and a graphical display.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py init
python manage.py status
python app.py
```

The local database `data/operations.db` is created automatically; `manage.py init`
also places **copies of both supplied template formats** in `data/` if missing.
Live input files and the SQLite database are excluded from Git.

**Run without a browser or GUI:**

```bash
python manage.py import --csv templates/WhatsAppData_staging.csv --excel templates/WhatsAppData.xlsx
python manage.py status
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The supplied files contain no records, so that import should insert **0** rows.
When importing populated files, keep the original 11-column headings and DMY
(`DD/MM/YYYY`) input dates. Duplicate messages across the CSV and workbook are
ignored by their unique fingerprints. The **demo CSV/XLSX** retain the 11 original
fields; privacy-minimised application exports omit raw `Full Message` text
(10 fields). Re-importing those stripped exports can produce a different
fingerprint from the original message and should not be used as a lossless
synchronization mechanism. Use the original staging files for reconciliation.

## Explore the populated synthetic demo

Open `notebooks/Operational_Intelligence_Demo.ipynb` in VS Code or Jupyter.
Its examples run locally, read the committed synthetic database and **do not
send data to an external service**. The demo includes 42 days of generated
reports across two non-real teams (168 reports in the default seed).

To create a new demo database in a different location:

```bash
python manage.py make-demo --output examples/my_fresh_demo.db
```

The CLI refuses to overwrite any existing DB. To explore the desktop application
with synthetic data, use the in-app **Add synthetic demonstration data** control
on a newly created local DB. Never mix synthetic and production records.

## Optional WhatsApp Web collection

The collector is **disabled by default**. For an authorized local test:

```bash
python -m playwright install chromium
python app.py
```

Use the application's monitoring controls and authenticate in the browser when
prompted. Change monitored group names in Settings to exact titles you are
authorized to observe. This uses a local WhatsApp Web browser session, not an
official WhatsApp API. The browser UI may change and collection is not
contractually guaranteed; review platform terms and privacy requirements.

Live records follow this pipeline:

`WhatsApp → parsed metrics → local staging CSV + Excel → SQLite → executive dashboard`

Startup also reconciles existing staging CSV entries into SQLite; the import
uses a unique fingerprint to avoid duplicates.

## Features

- Data quality: explicit DMY/MDY separation; missing values remain null;
  invalid dates are rejected; completely empty template rows are ignored.
- Local SQLite: durable report store, alert and audit tables, deduplication,
  monitored status, backup, and optional database relocation.
- Executive UI: time and group filters, workforce totals, near-miss charts,
  alerts, monitoring health, reports, theme toggle, CSV/Excel/PDF/offline HTML exports.
- Offline analytics: trend summaries, scenario projections and explicitly
  labelled synthetic demonstration data.

**Interpretation:** Counts derived from group messages are **unverified reported
values**, not confirmed incidents, verified employee attendance, or validated
compliance metrics. The original UI is desktop-based (not a hosted website).

## Engineering status and known limitations

Automated tests cover CSV/Excel template compatibility, date semantics, blank
rows, idempotency, the SQLite schema, and core summaries. Full graphical UI,
browser selectors, network connectivity and WhatsApp authentication require
manual testing on a supported desktop; they are not simulated by the tests.
The collector uses screen/UI scraping rather than an API and may need selector
updates as WhatsApp Web changes.

The manual change/no-show/shortage categories discussed for later expansion
**are not present in the supplied two files**; this release does not fabricate
those measures. See `docs/architecture.md` for the integration boundary.

## Release checklist

## Documentation

For a deeper understanding of the platform's design, implementation, and data management practices, refer to the following technical documentation:

- [System Architecture](https://github.com/SamudraDeSilva/WhatsApp_operational_intelligence/blob/main/docs/architecture.md) — Application architecture, component interactions, and data processing workflows.
- [Data Dictionary](https://github.com/SamudraDeSilva/WhatsApp_operational_intelligence/blob/main/docs/data_dictionary.md) — Database structure, field definitions, and operational reporting schema.
- [Security Guidelines](https://github.com/SamudraDeSilva/WhatsApp_operational_intelligence/blob/main/SECURITY.md) — Data protection, privacy considerations, and responsible deployment practices.

## Project Status

**Version:** 2.8 — Demonstration Release

This repository contains a demonstration implementation of the Operational Intelligence Platform, including synthetic datasets, automated reporting workflows, and executive analytics capabilities.

Live integrations and deployment-specific configurations require additional setup, testing, and appropriate authorisation.

See [the data dictionary](docs/data_dictionary.md),
[architecture](docs/architecture.md) and [security guidance](SECURITY.md).
