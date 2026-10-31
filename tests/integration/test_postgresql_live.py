"""Only the separate test-postgresql lab may receive fixture mutations."""

import asyncio
import os
import subprocess
import sys
import threading
import time
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from dbic.main import create_app
from dbic.plugins.postgresql.diagnostics import PostgreSQLDiagnostics
from dbic.settings import Settings
from tests.unit.test_mcp import rpc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_POSTGRESQL_TESTS") != "1", reason="requires isolated test-postgresql"
    ),
]


@pytest.fixture(scope="module")
def pg_lab():
    config = Settings()
    config = Settings.model_validate(
        config.model_dump()
        | {
            "postgresql_enabled": True,
            "mcp_enabled": True,
            "database_url": config.postgres_url()
            .set(database="dbic_test")
            .render_as_string(hide_password=False),
        }
    )
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=os.environ
            | {"DBIC_DATABASE_URL": config.postgres_url().render_as_string(hide_password=False)},
            check=True,
        )
    yield config


def admin():
    return psycopg.connect(
        host="postgresql-lab",
        dbname="dbic_lab",
        user="lab_admin",
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=2,
        options="-c statement_timeout=10000",
        autocommit=True,
    )


def diagnose(config):
    headers = {"Authorization": "Bearer " + config.api_token.get_secret_value()}
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/v1/diagnoses",
            headers=headers,
            json={"instance_id": "postgresql-lab-01", "problem_type": "maintenance_health"},
        )
        assert created.status_code == 202, created.text
        path = "/api/v1/diagnoses/" + created.json()["task_id"]
        detail = client.get(path, headers=headers).json()
        evidence = client.get(path + "/evidence", headers=headers).json()
        assert (
            client.post(
                path + "/feedback", headers=headers, json={"verdict": "partial"}
            ).status_code
            == 201
        )
    with TestClient(create_app(config)) as client:
        assert client.get(path + "/evidence", headers=headers).json() == evidence
        assert len(client.get(path, headers=headers).json()["feedback"]) == 1
    assert detail["task"]["status"] == "partial" and detail["report"]["agent_status"] == "disabled"
    assert len(evidence) == len(detail["steps"]) == 2
    assert all(
        f["evidence_ids"] and set(f["evidence_ids"]) <= {e["evidence_id"] for e in evidence}
        for f in detail["report"]["findings"]
    )
    return detail, evidence


def test_idle_and_mcp(pg_lab):
    detail, evidence = diagnose(pg_lab)
    assert (
        next(e for e in evidence if e["source"].endswith("blocking_sessions"))["status"] == "empty"
    )
    headers = {
        "Authorization": "Bearer " + pg_lab.api_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(create_app(pg_lab), base_url="http://127.0.0.1") as client:
        for name in ("postgresql_blocking_sessions", "postgresql_vacuum_health"):
            result = rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": "postgresql-lab-01"}},
                headers=headers,
            ).json()["result"]
            assert not result.get("isError")
            item = result["structuredContent"]
            assert client.get(
                "/api/v1/diagnoses/" + item["task_id"] + "/evidence", headers=headers
            ).json() == [item]


def test_real_row_lock_and_tuple_statistics(pg_lab):
    table = sql.Identifier("fixture_" + uuid4().hex)
    errors = []
    with admin() as setup, admin() as blocker, admin() as waiter:
        setup.execute(
            sql.SQL(
                "CREATE TABLE {} (id int primary key, value int) WITH (autovacuum_enabled=false)"
            ).format(table)
        )
        thread = None
        try:
            setup.execute(
                sql.SQL("INSERT INTO {} SELECT i, 0 FROM generate_series(1,4000) i").format(table)
            )
            blocker.execute("BEGIN")
            blocker.execute(sql.SQL("UPDATE {} SET value=1 WHERE id=1").format(table))

            def wait():
                try:
                    waiter.execute("BEGIN")
                    waiter.execute(sql.SQL("UPDATE {} SET value=2 WHERE id=1").format(table))
                except Exception as exc:
                    errors.append(type(exc).__name__)
                finally:
                    waiter.execute("ROLLBACK")

            thread = threading.Thread(target=wait)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if setup.execute(
                    "SELECT pg_blocking_pids(%s)", (waiter.info.backend_pid,)
                ).fetchone()[0]:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("test row lock did not appear")
            detail, evidence = diagnose(pg_lab)
            sessions = next(e for e in evidence if e["source"].endswith("blocking_sessions"))[
                "payload"
            ]["sessions"]
            assert any(
                s["waiter_pid"] == waiter.info.backend_pid
                and blocker.info.backend_pid in s["blocker_pids"]
                for s in sessions
            )
            assert any(
                f["rule_id"] == "postgresql.blocking.observed" for f in detail["report"]["findings"]
            )
            blocker.execute("ROLLBACK")
            thread.join(12)
            assert not thread.is_alive() and not errors
            setup.execute(sql.SQL("UPDATE {} SET value=value+1 WHERE id<=2000").format(table))
            setup.execute(sql.SQL("ANALYZE {}").format(table))
            # Stats are published asynchronously; flush only this fixture's backend.
            setup.execute("SELECT pg_stat_force_next_flush()")
            _, evidence = diagnose(pg_lab)
            vacuum = next(e for e in evidence if e["source"].endswith("vacuum_health"))
            assert vacuum["status"] == "ok" and any(
                t["dead_tuples_estimate"] >= 1000 for t in vacuum["payload"]["tables"]
            )
            assert "fixture_" not in str(evidence) and "UPDATE" not in str(evidence)
        finally:
            blocker.execute("ROLLBACK")
            if thread:
                thread.join(12)
            setup.execute(sql.SQL("DROP TABLE {}").format(table))


def test_minimal_role_and_visibility_failure(pg_lab):
    with (
        psycopg.connect(
            host="postgresql-lab",
            dbname="dbic_lab",
            user="diag",
            password=pg_lab.postgresql_diag_password.get_secret_value(),
            autocommit=True,
        ) as diag,
        admin() as root,
    ):
        assert diag.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
        assert diag.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False, False, False)
        assert not diag.execute(
            "SELECT pg_has_role(current_user,'pg_signal_backend','USAGE')"
        ).fetchone()[0]
        table = sql.Identifier("private_" + uuid4().hex)
        root.execute(sql.SQL("CREATE TABLE {} (id int)").format(table))
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                diag.execute(sql.SQL("SELECT * FROM public.{}").format(table))
            root.execute("REVOKE pg_read_all_stats FROM diag")
            try:
                _, evidence = diagnose(pg_lab)
                assert all(e["status"] == "forbidden" for e in evidence)
            finally:
                root.execute("GRANT pg_read_all_stats TO diag")
        finally:
            root.execute(sql.SQL("DROP TABLE {}").format(table))
    result = asyncio.run(
        PostgreSQLDiagnostics(pg_lab.postgresql_diag_password).run(
            tool_id="postgresql.blocking_sessions", instance_id="postgresql-lab-01", params={}
        )
    )
    assert result.status == "empty"
