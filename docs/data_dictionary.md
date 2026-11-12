# Data dictionary — v3.0.0

## Original staging structure (unchanged)

`Date`, `Time`, `Group`, `Sender`, `Full Message`, `T002/T003`, `PWT`, `SPL`, `PL`, `Near Miss`, `Late`.

Input date fields use `DD/MM/YYYY`; WhatsApp Web metadata is parsed separately.
Keep both the original CSV and Excel headers when using the collector or importer.

## SQLite data model

- `reports`: per-message records, timestamp, group, sender, original message, staffing-role metrics, `near_miss`, `late`, optional `no_shows`, origin, quality information and unique fingerprint.
- `staffing_plans`: imported, date-effective plans from visible Excel worksheets.
- `manual_staffing_plans`: manually entered role requirements with validity by start date.
- `manual_staffing_plan_audit`: historical edits to plan versions.
- `exception_entries`: per-group, per-service-date, per-shift changes before/after 15:00 and optional linked attendance snapshot. A NULL indicates unknown/not yet recorded, not zero.
- `exception_entry_audit`: historical changes to exceptions.
- `exception_sync_state`: internal revision state for cache/refresh invalidation.
- `app_users`: local account identifiers, roles, salted password hashes, recovery hashes, lockout and status metadata. The demo DB contains **no configured user accounts**.
- `exception_users`: compatibility for older single-operator credentials.
- `audit`, `alerts`, `monitor_state`, `collection_diagnostics`: reporting and operational diagnostics.

## Local file catalogue

`data/reports/report_catalog.json` indexes report paths, timestamps and selected scope. It is machine-local and deliberately excluded from the repository.

See `schema.sql` for a SQLite-only reference schema; the definitive creation and migration logic lives in `core.init_db()` and `core.init_exception_db()`.
