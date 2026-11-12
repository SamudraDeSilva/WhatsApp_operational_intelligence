-- Reference SQL from synthetic v3.0.0 database. Run core.init_db() and core.init_exception_db() for migrations.

CREATE TABLE alerts (
          id INTEGER PRIMARY KEY, alert_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
          level TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Open', owner TEXT DEFAULT '', resolved_at TEXT
        );

CREATE TABLE app_users (
          username TEXT PRIMARY KEY COLLATE NOCASE,
          display_name TEXT NOT NULL,
          account_role TEXT NOT NULL CHECK(account_role IN ('admin','operator')),
          password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          recovery_salt TEXT NOT NULL, recovery_hash TEXT,
          failed_attempts INTEGER NOT NULL DEFAULT 0,
          locked_until TEXT,
          recovery_used INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login TEXT
        );

CREATE TABLE audit (
          id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
        );

CREATE TABLE exception_entries (
          group_name TEXT NOT NULL, service_date TEXT NOT NULL,
          shift_label TEXT NOT NULL CHECK(shift_label IN ('Shift 1','Shift 2','Shift 3')),
          before_count INTEGER CHECK(before_count >= 0),
          before_impact INTEGER,
          after_count INTEGER CHECK(after_count >= 0),
          after_impact INTEGER,
          no_shows INTEGER CHECK(no_shows >= 0),
          basis TEXT NOT NULL DEFAULT 'already_actual'
              CHECK(basis IN ('pre_adjustments','already_actual')),
          report_id INTEGER,
          before_by TEXT, before_at TEXT, after_by TEXT, after_at TEXT,
          revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
          PRIMARY KEY (group_name, service_date, shift_label)
        );

CREATE TABLE exception_entry_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          changed_at TEXT NOT NULL, group_name TEXT NOT NULL,
          service_date TEXT NOT NULL, shift_label TEXT NOT NULL,
          actor_role TEXT NOT NULL, actor_name TEXT NOT NULL,
          old_state TEXT NOT NULL, new_state TEXT NOT NULL
        );

CREATE TABLE exception_sync_state (
          id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
        );

CREATE TABLE exception_users (
          role TEXT PRIMARY KEY CHECK(role IN ('before','after')),
          display_name TEXT NOT NULL,
          password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

CREATE TABLE manual_staffing_plan_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          changed_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          operation TEXT NOT NULL,
          group_name TEXT NOT NULL,
          effective_from TEXT NOT NULL,
          old_state TEXT,
          new_state TEXT
        );

CREATE TABLE manual_staffing_plans (
          group_name TEXT NOT NULL COLLATE NOCASE,
          effective_from TEXT NOT NULL,
          components_json TEXT NOT NULL,
          planned_total INTEGER NOT NULL CHECK(planned_total > 0),
          updated_at TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          PRIMARY KEY (group_name,effective_from)
        );

CREATE TABLE monitor_state (
          id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
          last_checked TEXT, last_success TEXT, last_error TEXT
        );

CREATE TABLE reports (
          id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
          event_at TEXT NOT NULL, captured_at TEXT NOT NULL,
          group_name TEXT NOT NULL, sender TEXT NOT NULL, message TEXT NOT NULL,
          t002_t003 INTEGER, pwt INTEGER, spl INTEGER, pl INTEGER,
          near_miss INTEGER, late INTEGER, origin TEXT NOT NULL DEFAULT 'import',
          quality_note TEXT DEFAULT ''
        , no_shows INTEGER);

CREATE TABLE staffing_plans (
          group_name TEXT NOT NULL, worksheet TEXT NOT NULL,
          effective_from TEXT NOT NULL, effective_to TEXT NOT NULL,
          components_json TEXT NOT NULL, planned_total INTEGER NOT NULL,
          workbook_name TEXT NOT NULL, source_cell TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          PRIMARY KEY (group_name, worksheet, effective_from, effective_to)
        );

CREATE INDEX idx_exception_date ON exception_entries(service_date);

CREATE INDEX idx_manual_staffing_effective
          ON manual_staffing_plans(group_name,effective_from);

CREATE INDEX idx_reports_event ON reports(event_at);

CREATE INDEX idx_reports_group ON reports(group_name);

CREATE INDEX idx_staffing_plans_dates
          ON staffing_plans(group_name, effective_from, effective_to);

CREATE UNIQUE INDEX idx_exception_snapshot_unique
          ON exception_entries(report_id) WHERE report_id IS NOT NULL;
