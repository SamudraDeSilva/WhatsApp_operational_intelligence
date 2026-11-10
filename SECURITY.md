# Security and responsible publication

This project is a local demonstration of a reporting workflow. It is **not** a public
WhatsApp reporting service, and it provides no warrant to collect other people's messages.

Before publishing or deploying, obtain permission from the owner of the source code,
the organisation operating the monitored groups, and the relevant data controller.
Internal chat transcripts, employee names, phone numbers, site identifiers and live
operational KPIs should not be uploaded to GitHub or third-party hosted services.

The `data/` directory, `whatsapp_session/`, local configuration files, exported reports,
SQLite WAL files and unredacted CSV/Excel files are deliberately gitignored. Do not
force-add those files, and inspect both Git commits and notebook outputs for sensitive
data before making a repository public. Browser sessions contain sensitive state.

`examples/demo_operations.db` and the sample exports contain labelled **synthetic**
records only. Tests run against temporary databases, not production data.

The optional WhatsApp Web collector runs only after a user signs in and explicitly
starts monitoring. Browser scraping is dependent on WhatsApp's changing UI and may
be subject to platform terms; assess permitted use and applicable privacy obligations
before enabling it. There is no official WhatsApp API integration in this release.

If real sensitive data was committed, removing it from the latest commit is not
sufficient: rewrite repository history, rotate exposed sessions/secrets and notify the
appropriate data owner. No secrets are required for offline mode.
