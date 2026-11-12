# Rail Operational Intelligence — v3.0.0 | Overview & Reporting Hub

This GitHub release provides the latest application notebook and modular Python implementation under version **v3.0.0**. It introduces a broader local-first operational intelligence workflow while retaining the original WhatsApp reporting integration and 11-column CSV/Excel import formats.

### New
- Executive Overview, refined navigation, collapsible sidebar and searchable Reports hub.
- Dedicated Exceptions workflow for logging staffing changes before and after 15:00.
- Date-effective staffing plans, manual revisions and supported read-only Excel tracker imports.
- Local administrator/operator accounts, secure salted password hashes and recovery codes.
- Expanded SQLite database and exception-aware PDF/standalone HTML reporting.

### Quality and compatibility
- Original CSV/Excel templates retained; blank template rows ignored.
- Synthetic example database upgraded to the new schema, with 168 reports, 2 plans and 8 exception entries. No real employee data or credentials.
- 14 automated regression tests passed, and a synthetic HTML/PDF export smoke test succeeded in the development environment.

### Known limitations
The GUI and authorized WhatsApp Web collection require testing on a real desktop. This is not an official WhatsApp API integration or a hosted multi-user service. Confirm source-code publication rights before making the repository public.
