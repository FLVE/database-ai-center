import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

from dbic.adapters.persistence.postgres import SCHEMA_REVISION, database_ready
from dbic.settings import Settings


@pytest.mark.integration
def test_postgres_upgrade_is_repeatable(monkeypatch):
    url = os.environ.get("DBIC_TEST_DATABASE_URL")
    if not url:
        pytest.skip("DBIC_TEST_DATABASE_URL not set; real PostgreSQL lab required")
    monkeypatch.setenv("DBIC_DATABASE_URL", url)
    monkeypatch.setenv("DBIC_API_TOKEN", "integration-test-token-not-real")
    config = Settings()
    if config.postgres_url().database != "dbic_test":
        pytest.fail("integration tests require the dedicated dbic_test database")
    for _ in range(2):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    engine = create_engine(config.postgres_url())
    try:
        assert database_ready(engine)
        assert set(inspect(engine).get_table_names()) >= {
            "instances",
            "diagnostic_tasks",
            "diagnostic_steps",
            "evidence",
            "findings",
            "reports",
            "feedback",
            "audit_events",
            "alembic_version",
        }
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == SCHEMA_REVISION
            )
    finally:
        engine.dispose()
