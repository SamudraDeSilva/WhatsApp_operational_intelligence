# Data dictionary and source compatibility

The uploaded `WhatsAppData.xlsx` and `WhatsAppData_staging(1).csv` were both
**header-only**. Their shared column order is retained in the templates, with
blank preformatted CSV rows removed. The filename `WhatsAppData_staging(1).csv`
was normalized to `WhatsAppData_staging.csv` for the application.

| Source column | SQLite column | Expected interpretation |
|---|---|---|
| Date | `event_at` (date component) | DMY / ISO accepted from CSV or Excel |
| Time | `event_at` (time component) | 24-hour or 12-hour time |
| Group | `group_name` | Exact reported group title |
| Sender | `sender` | Reported message sender |
| Full Message | `message` | Full source text; sensitive if real |
| T002/T003 | `t002_t003` | Non-negative, reported count |
| PWT | `pwt` | Non-negative, reported count |
| SPL | `spl` | Non-negative, reported count |
| PL | `pl` | Non-negative, reported count |
| Near Miss | `near_miss` | Reported count, not a verified incident count |
| Late | `late` | Reported count |

The database additionally includes `id`, `fingerprint`, `captured_at`, `origin`
and `quality_note`. `fingerprint` supports idempotent re-import. Missing metric
values are **NULL**, not zero. Completely blank input rows are skipped.

The sample database is labelled synthetic (`origin='demo'`) and is not derived
from real WhatsApp activity. Demo dates are generated relative to the run date.

Source names `T002/T003`, `PWT`, `SPL`, and `PL` are preserved without inventing
business definitions that were not provided. Clarify these category definitions
before interpreting totals as headcount, attendance compliance or site KPIs.
