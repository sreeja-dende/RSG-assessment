"""Test setup.

Environment variables are set BEFORE the app is imported, so:
- tests use a temporary database, never data/sales.db
- the LLM is off (rules mode), so tests are free, fast, offline and repeatable.
LLM behaviour is tested separately with a fake `generate` function.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["LLM_PROVIDER"] = "mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ingest import load_csv  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def loaded_db():
    """Load the real supplied CSV into the temp database once."""
    return load_csv()


@pytest.fixture()
def client(loaded_db):
    with TestClient(app) as c:
        yield c


def ask(client, question):
    response = client.post("/api/ask", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()
