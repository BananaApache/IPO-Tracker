"""Test-suite guards.

The suite reads configuration through `get_settings()`, which reads `.env` --
so it connects wherever `.env` points. Once `.env` was filled in for the
deployed environment, `pytest` started opening transactions against the
production Neon database. Every test rolls back, so nothing was lost, but that
is luck rather than design: one test that forgets a rollback, or one `DELETE`
outside a transaction, and the damage is real and remote.

So the suite refuses to run against a non-local host. Override deliberately
with TEST_ALLOW_REMOTE_DB=1 if you ever genuinely need to.
"""

import os

import pytest

from backend.config import get_settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}


def pytest_sessionstart(session: pytest.Session) -> None:
    host = get_settings().postgres_host
    if host in _LOCAL_HOSTS or os.environ.get("TEST_ALLOW_REMOTE_DB") == "1":
        return
    pytest.exit(
        f"refusing to run tests against a non-local database: {host}\n"
        "  .env is pointed at a deployed environment. Either run with local\n"
        "  Postgres settings (POSTGRES_HOST=localhost ...), or set\n"
        "  TEST_ALLOW_REMOTE_DB=1 if you really mean it.",
        returncode=2,
    )
