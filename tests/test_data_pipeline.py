import csv
from datetime import datetime
from pathlib import Path

import core

ROOT = Path(__file__).resolve().parents[1]
HEADERS = core.FIELDS


def test_uploads_have_matching_headers_and_no_real_rows(empty_db):
    csv_path = ROOT / 'templates' / 'WhatsAppData_staging.csv'
    xlsx_path = ROOT / 'templates' / 'WhatsAppData.xlsx'
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        assert next(csv.reader(f)) == HEADERS
    assert core.import_csv(csv_path) == (0, 0, [])
    assert core.import_excel(xlsx_path) == (0, 0, [])
    assert core.filtered() == []


def test_empty_spreadsheet_rows_are_ignored(empty_db, tmp_path):
    p = tmp_path / 'empty.csv'
    with p.open('w', newline='', encoding='utf-8') as stream:
        w = csv.writer(stream)
        w.writerow(HEADERS)
        w.writerow([''] * 11)
        w.writerow(['  '] + [''] * 10)
    assert core.import_csv(p) == (0, 0, [])


def test_dmy_csv_and_idempotent_import(empty_db, tmp_path):
    p = tmp_path / 'message.csv'
    with p.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerow({'Date': '10/11/2026', 'Time': '14:30:00', 'Group': 'Demo Team',
                    'Sender': 'Demo Person', 'Full Message': 'PWT: 4 Near Miss: 0 Late: 1',
                    'PWT': 4, 'Near Miss': 0, 'Late': 1})
    assert core.import_csv(p) == (1, 0, [])
    assert core.import_csv(p) == (0, 1, [])
    rows = core.filtered()
    assert len(rows) == 1
    assert rows[0]['event_at'] == '2026-11-10T14:30:00'
    assert rows[0]['pwt'] == 4
    assert rows[0]['near_miss'] == 0
    assert rows[0]['t002_t003'] is None  # Unknown is not a zero.


def test_summary_aggregates_and_tracks_missing_safety(empty_db):
    core.insert_report('Demo Team','Demo Person','PWT: 5',
                       datetime(2026,11,10,9), {'PWT': 5}, 'test')
    result = core.summary(core.filtered(), expected_groups=['Demo Team', 'Other Team'])
    assert result['reports'] == 1
    assert result['staffing']['pwt'] == 5
    assert result['missing_safety'] == 1
    assert result['missing_groups'] == ['Other Team']
    assert result['coverage'] == 50


def test_invalid_dates_are_rejected_not_americanised(empty_db, tmp_path):
    p = tmp_path / 'invalid.csv'
    with p.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerow({'Date': '31/13/2026', 'Time':'09:00','Group':'Demo Team',
                    'Full Message':'Late: 1'})
    added, dupes, errors = core.import_csv(p)
    assert (added, dupes) == (0,0)
    assert len(errors) == 1


def test_sqlite_schema_and_integrity(empty_db):
    with core.connect() as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'reports', 'alerts', 'audit', 'monitor_state'}.issubset(tables)


def test_cross_file_example_import_is_idempotent(empty_db):
    csv_file = ROOT / 'examples' / 'demo_WhatsAppData.csv'
    workbook = ROOT / 'examples' / 'demo_WhatsAppData.xlsx'
    assert core.import_csv(csv_file) == (168, 0, [])
    assert core.import_excel(workbook) == (0, 168, [])
    assert len(core.filtered()) == 168
