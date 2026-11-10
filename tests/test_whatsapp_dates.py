"""Collector date contract, without starting Chromium or signing in."""
import ast
from pathlib import Path

import core


def test_british_dates_and_iso_are_distinct():
    assert core.normalise_datetime('10/11/2026','13:15:00') == '2026-11-10T13:15:00'
    assert core.normalise_datetime('2026-11-10','13:15') == '2026-11-10T13:15:00'


def test_source_explicitly_declares_whatsapp_mdy():
    source = (Path(__file__).resolve().parents[1] / 'collector.py').read_text(encoding='utf-8')
    root = ast.parse(source)
    assignment = [stmt for stmt in root.body if isinstance(stmt, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'WHATSAPP_NUMERIC_DATE_ORDER'
                          for t in stmt.targets)]
    assert len(assignment) == 1
    assert ast.literal_eval(assignment[0].value) == 'MDY'


def test_actual_timestamp_parser_without_browser_dependency():
    """Test original parsing function without importing Playwright or tkinter."""
    import re
    from datetime import datetime, timedelta
    source = (Path(__file__).resolve().parents[1] / 'collector.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    definition = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'parse_whatsapp_timestamp')
    body = ast.Module(body=[definition], type_ignores=[])
    ns = {'re': re, 'datetime': datetime, 'timedelta': timedelta,
          'WHATSAPP_NUMERIC_DATE_ORDER': 'MDY', 'CSV_DATE_OUTPUT_FORMAT': '%d/%m/%Y'}
    exec(compile(body, 'collector_timestamp_function', 'exec'), ns)
    parser = ns['parse_whatsapp_timestamp']
    assert parser('[10:44, 11/10/2026] Team:') == ('10/11/2026', '10:44:00')
    assert parser('[18:38, 11/09/2026] Team:') == ('09/11/2026', '18:38:00')
    assert parser('[12:56, 11/06/2026] Team:') == ('06/11/2026', '12:56:00')
    import pytest
    with pytest.raises(ValueError):
        parser('[12:56, 18/13/2026] Team:')
