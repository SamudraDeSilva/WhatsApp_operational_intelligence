"""Offline command line administration for Operational Intelligence.

CLI intentionally does not import the Playwright/desktop components.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import core

PROJECT = Path(__file__).resolve().parent
TEMPLATES = PROJECT / 'templates'


def bootstrap():
    core.init_db()
    core.DATA.mkdir(parents=True, exist_ok=True)
    for filename in ('WhatsAppData.xlsx', 'WhatsAppData_staging.csv'):
        destination = core.DATA / filename
        if not destination.exists():
            shutil.copy2(TEMPLATES / filename, destination)
    print(f'SQLite ready: {core.DB}')
    print(f'Local spreadsheet/CSV templates: {core.DATA}')


def ingest(csv_paths, excel_paths):
    core.init_db()
    if not csv_paths and not excel_paths:
        raise ValueError('Specify --csv and/or --excel to import data.')
    total_new = total_dupes = total_errors = 0
    for kind, files in (('CSV', csv_paths), ('XLSX', excel_paths)):
        for path in files:
            source = Path(path).expanduser().resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            added, dupes, errors = (core.import_csv(source) if kind == 'CSV' else core.import_excel(source))
            print(f'{kind} {source.name}: {added} inserted, {dupes} duplicates, {len(errors)} rejected')
            for error in errors[:10]:
                print('  REJECTED', error)
            total_new += added
            total_dupes += dupes
            total_errors += len(errors)
    print(f'TOTAL: inserted={total_new}; duplicates={total_dupes}; rejected={total_errors}')
    if total_errors:
        raise SystemExit(2)


def show_status():
    with core.connect() as conn:
        row = conn.execute('SELECT COUNT(*) AS count, COUNT(DISTINCT group_name) AS teams, '
                           'MIN(event_at) AS first_date, MAX(event_at) AS last_date FROM reports').fetchone()
    print(f'Database: {core.DB}')
    print(f'Reports: {row["count"]}; teams: {row["teams"]}')
    print(f'Period: {row["first_date"] or "—"} to {row["last_date"] or "—"}')


def create_demo(path):
    output = Path(path).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f'{output} already exists. Refusing to overwrite any database.')
    output.parent.mkdir(parents=True, exist_ok=True)
    old = core.DB
    try:
        core.DB = output
        core.init_db()
        count = core.demo_data(days=42, seed=42)
        with core.connect() as conn:
            ok = conn.execute('PRAGMA integrity_check').fetchone()[0]
            origins = set(x[0] for x in conn.execute('SELECT DISTINCT origin FROM reports'))
            assert ok == 'ok' and origins == {'demo'}, (ok, origins)
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        print(f'Created {output} with {count} synthetic records. No real messages were copied.')
    finally:
        core.DB = old


def main(argv=None):
    parser = argparse.ArgumentParser(description='Operational Intelligence data and database manager')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='Create a local private SQLite database and copy the blank templates')
    import_cmd = sub.add_parser('import', help='Idempotently import the supplied CSV and/or Excel schema')
    import_cmd.add_argument('--csv', action='append', default=[], metavar='FILE')
    import_cmd.add_argument('--excel', action='append', default=[], metavar='FILE')
    sub.add_parser('status', help='Summarise reports in the active database')
    demo = sub.add_parser('make-demo', help='Generate a separate labelled synthetic SQLite database')
    demo.add_argument('--output', default=str(PROJECT / 'examples' / 'demo_operations.db'))
    args = parser.parse_args(argv)
    if args.command == 'init':
        bootstrap()
    elif args.command == 'import':
        ingest(args.csv, args.excel)
    elif args.command == 'status':
        show_status()
    elif args.command == 'make-demo':
        create_demo(args.output)


if __name__ == '__main__':
    main()
