"""v3.0.0 database and account regression tests, using isolated temporary SQLite databases."""
import sqlite3
import pytest
import core


def test_v3_schema_tables_present(empty_db):
    with core.connect() as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {r[1] for r in con.execute('PRAGMA table_info(reports)')}
    assert {'exception_entries', 'staffing_plans', 'manual_staffing_plans',
            'app_users', 'exception_entry_audit', 'exception_sync_state'}.issubset(tables)
    assert 'no_shows' in columns


def test_manual_staffing_plan_and_separate_change_stages(empty_db):
    plan = core.save_manual_staffing_plan('Gang 1', '2026-11-10',
                {'T002/T003': 3, 'PWT': 6, 'SPL': 2, 'PL': 1}, actor='SYNTHETIC TEST')
    assert plan['planned_total'] == 12
    assert core.exception_plan('Gang 1', '2026-11-10')[0]['planned_total'] == 12
    first = core.save_change_stage('Demo Operator', 'Gang 1', '2026-11-10',
                                   'Shift 1', 0, 'before', 2)
    assert first['before_count'] == 2 and first['after_count'] is None
    second = core.save_change_stage('Demo Operator', 'Gang 1', '2026-11-10',
                                    'Shift 1', first['revision'], 'after', 1)
    assert second['before_count'] == 2 and second['after_count'] == 1
    with pytest.raises(RuntimeError):
        core.save_change_stage('Demo Operator', 'Gang 1', '2026-11-10',
                               'Shift 1', 0, 'after', 3)
    result = core.exception_dashboard('2026-11-10', '2026-11-10', 'Gang 1')
    assert result['changes_before'] == 2 and result['changes_after'] == 1
    assert result['awaiting_whatsapp'] >= 1


def test_multi_user_authentication_and_one_time_recovery(empty_db):
    assert not core.app_users_ready()
    recovery = core.bootstrap_admin_account('demo_admin', 'Synthetic Admin', 'Testing-Only-Pass-0001')
    assert recovery and core.app_users_ready()
    with pytest.raises(PermissionError):
        core.authenticate_app_user('demo_admin', 'incorrect')
    session = core.authenticate_app_user('demo_admin', 'Testing-Only-Pass-0001')
    token = session['token']
    assert core.app_session_info(token)['role'] == 'admin'
    code = core.create_app_user(token, 'demo_operator', 'Synthetic Operator',
                                'operator', 'Testing-Only-Pass-0002')
    assert code and len(core.list_app_users()) == 2
    refreshed_code = core.recover_app_password('demo_admin', recovery,
                                               'Testing-Only-Pass-NEW1')
    assert refreshed_code and refreshed_code != recovery
    with pytest.raises(PermissionError):
        core.app_session_info(token)  # Recovery invalidates existing sessions.
    with pytest.raises(PermissionError):
        core.recover_app_password('demo_admin', recovery, 'Testing-Only-Pass-NEW2')


def test_missing_no_shows_remain_unknown(empty_db):
    from datetime import datetime
    core.insert_report('Gang 1', 'SYNTHETIC', 'PWT: 5',
                       datetime(2026, 11, 10, 8, 0), {'PWT': 5}, 'demo')
    with core.connect() as con:
        value=con.execute('SELECT no_shows FROM reports').fetchone()[0]
    assert value is None
