"""Test setup: fresh temporary database + mock LLM for every test run.

Env vars are set BEFORE importing the app, so the app picks them up.
Tests never touch the real app.db or call a paid API.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_MAX_RETRIES"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)  # clean slate per test
    with TestClient(app) as c:
        yield c
