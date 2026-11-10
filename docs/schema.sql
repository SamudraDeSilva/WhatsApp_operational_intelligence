-- SQLite schema for Operational Intelligence. See core.init_db() for runtime initialization.

CREATE TABLE alerts (
          id INTEGER PRIMARY KEY, alert_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
          level TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Open', owner TEXT DEFAULT '', resolved_at TEXT
        );

CREATE TABLE audit (
          id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
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
        );

CREATE INDEX idx_reports_event ON reports(event_at);

CREATE INDEX idx_reports_group ON reports(group_name);

