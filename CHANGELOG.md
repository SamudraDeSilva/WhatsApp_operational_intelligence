# Changelog

## v3.0.0 — Overview & Reporting Hub (2026-11-12)

**Source:** Latest supplied application notebook, compared with the original v2.8 notebook and earlier GitHub-ready package.

### Added
- Overview landing page and Reports hub, with local searchable report catalogue.
- Navigation refinements, collapsible sidebar and brief loading state.
- Operator workflow for change counts recorded before and after 15:00.
- Staffing plan management, including date-effective manual versions and supported Excel tracker import.
- Local administrator/operator accounts, hashed passwords, recovery codes and session control.
- Expanded SQLite tables for staffing plans, exception entries, history and user administration.
- Joined operational exceptions in PDF and standalone interactive HTML exports.
- Explicit no-show count capture, while retaining unknown/null for missing data.

### Changed
- Updated the modular application core, collector and desktop GUI using the v3.0.0 notebook implementation.
- Kept original WhatsApp message parser and existing 11-column CSV/Excel interfaces.
- Added regression tests for exception workflow, schema migration, authentication and recovery.
- Kept blank-row handling from the earlier GitHub package.

### Limitations
- GUI, WhatsApp authentication and live external websites were not tested in CI.
- The new authentication is for a single local workstation, not a network-accessible identity system.
- Source-code publication requires permission from the legal owner; use synthetic datasets only.

## v2.8 — Original GitHub package (2026-11-10)

Original desktop executive dashboards, local SQLite storage, authorized optional WhatsApp collection, supplied CSV/Excel templates, synthetic dataset and initial automated tests.
