# Application comparison and migration: v2.8 → v3.0.0

| Capability | v2.8 notebook / initial GitHub package | v3.0.0 notebook / updated package |
|---|---|---|
| WhatsApp Web collection | Optional browser-based collector | Collector workflow retained; minor structural comments |
| CSV and Excel inputs | Original 11 fields, UK DMY | Same 11 fields, explicit no-shows can be parsed from message text |
| SQLite | Reports, monitoring and alerts | Adds planned staffing, exception entries/audit, user accounts and no-shows |
| UI | Workforce, Executive, safety and settings | Adds Overview, Exceptions and Reports hub with refreshed tables/navigation |
| Deadline changes | Not represented as dedicated records | Before / after 15:00 events, separate saving and revision checks |
| Staffing plans | No plan versioning | Excel tracker read-only import, dated manual plan revisions |
| Access management | No app-specific named accounts | Local administrator/operator accounts, recovery and session management |
| Export | CSV/XLSX/PDF/HTML | Expanded PDF and standalone HTML to incorporate exception reporting |
| Report library | No dedicated searchable catalogue | Local searchable catalogue at `data/reports/report_catalog.json` |
| Automated checks | Data/date/import tests | Adds schema, exception, account and migration checks |

**Database migration:** The v3.0.0 application creates additional tables on startup and adds a nullable `no_shows` column to existing `reports`. Always make an offline backup of `data/operations.db` before opening the existing database under new code. Run `python manage.py init` and review `python manage.py status`. Never publish a migrated live database. Full downgrade behavior has not been established.

**Source note:** The latest supplied notebook describes a spacing/theme-only refinement of the preceding Overview & Reporting Hub implementation. The broader differences above compare the original and latest user-provided notebooks, not just the final visual refinement.
