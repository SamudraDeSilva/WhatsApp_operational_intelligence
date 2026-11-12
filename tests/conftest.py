"""Fresh local SQLite per test: cannot read or modify the user's active DB."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DB', tmp_path / 'test_operations.db')
    core.init_db()
    core.init_exception_db()
    return core
