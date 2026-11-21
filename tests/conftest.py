"""
The storage and API tests run on the JSON file by default. With DATABASE_URL set they run on PostgreSQL
instead, starting every test from an empty table:  DATABASE_URL=postgresql://... pytest tests
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from werkzeug.security import generate_password_hash

TEST_USERNAME = "owner"
TEST_PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def login_credentials(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("APP_PASSWORD_HASH", generate_password_hash(TEST_PASSWORD))
    yield


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch):
    import storage

    if os.environ.get("DATABASE_URL"):
        with storage._connect() as conn:
            conn.execute("TRUNCATE subscriptions")
        yield
        return

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(storage, "DATA_PATH", Path(tmp) / "subscriptions.json")
        yield
