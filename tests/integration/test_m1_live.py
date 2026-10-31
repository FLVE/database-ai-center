"""Only the isolated Compose verify profile enables these destructive lab fixtures."""

import asyncio
import os
import secrets
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pymysql
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from dbic.adapters.persistence.tasks import PostgresTaskStore
from dbic.core.engine import transition_task
from dbic.core.models import DiagnosticTask, Report, TaskStatus
from dbic.main import create_app
from dbic.plugins.mysql.diagnostics import MySQLDiagnostics
from dbic.settings import Settings


def test_live_history_persistence(lab, monkeypatch):
    import httpx

    from dbic.adapters.history_factory import historical_adapters
    from tests.unit.test_history_adapters import WINDOW, matrix, search_response

    settings, _ = lab
    config = settings.model_copy(
        update={
            "metrics_backend": "prometheus",
            "search_backend": "elastic",
            "prometheus_url": "http://localhost:9090",
            "elastic_url": "http://localhost:9200",
            "prometheus_instance_value": "mysql-lab:9104",
        }
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=matrix() if request.method == "GET" else search_response()
        )
    )
    monkeypatch.setattr(
        "dbic.main.historical_adapters", lambda _: historical_adapters(config, transport=transport)
    )
    headers = {"Authorization": "Bearer " + settings.api_token.get_secret_value()}
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=headers,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait", "window": WINDOW},
        )
        assert response.status_code == 202
        path = "/api/v1/diagnoses/" + response.json()["task_id"]
    # A new app and store must reconstruct the historical window and references from PostgreSQL.
    with TestClient(create_app(settings)) as client:
        detail = client.get(path, headers=headers).json()
        evidence = client.get(path + "/evidence", headers=headers).json()
    assert len(detail["steps"]) == len(evidence) == 4
    assert detail["task"]["status"] in {"completed", "partial"}
    history = [item for item in evidence if item["kind"] in {"metric", "log"}]
    assert len(history) == 2
    assert all(item["window"] == detail["task"]["window"] for item in history)
    ids = {item["evidence_id"] for item in evidence}
    assert all(set(finding["evidence_ids"]) <= ids for finding in detail["report"]["findings"])


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_LIVE_TESTS") != "1", reason="requires isolated Compose verify profile"
    ),
]


class SecretOptions(dict):
    def __repr__(self):
        return "<mysql-lab connection options redacted>"


@pytest.fixture(scope="module")
def lab():
    config = Settings()
    url = config.postgres_url().set(database="dbic_test")
    environment = os.environ | {"DBIC_DATABASE_URL": url.render_as_string(hide_password=False)}
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True
        )
    root_options = SecretOptions(
        host="mysql-lab",
        user="root",
        password=os.environ["DBIC_TEST_MYSQL_ROOT_PASSWORD"],
        autocommit=True,
        connect_timeout=5,
        read_timeout=15,
        write_timeout=5,
    )
    password = secrets.token_hex(32)
    with pymysql.connect(**root_options) as conn, conn.cursor() as cursor:
        cursor.execute("CREATE USER IF NOT EXISTS 'diag'@'%%' IDENTIFIED BY %s", (password,))
        cursor.execute("ALTER USER 'diag'@'%%' IDENTIFIED BY %s", (password,))
        for table in (
            "global_status",
            "data_lock_waits",
            "data_locks",
            "threads",
            "setup_consumers",
            "setup_instruments",
            "events_statements_summary_by_digest",
        ):
            cursor.execute(f"GRANT SELECT ON performance_schema.{table} TO 'diag'@'%'")
        cursor.execute("CREATE DATABASE IF NOT EXISTS dbic_fixture")
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS dbic_fixture.accounts (id INT PRIMARY KEY, value INT)"
        )
        cursor.execute("INSERT IGNORE INTO dbic_fixture.accounts VALUES (1, 0)")
    settings = Settings(
        database_url=url.render_as_string(hide_password=False),
        api_token=config.api_token,
        mysql_diag_password=password,
    )
    yield settings, root_options


def diagnose(settings, problem):
    headers = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=headers,
            json={"instance_id": "mysql-lab-01", "problem_type": problem},
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        detail = client.get(f"/api/v1/diagnoses/{task_id}", headers=headers).json()
        evidence = client.get(f"/api/v1/diagnoses/{task_id}/evidence", headers=headers).json()
        assert len(detail["steps"]) == 2
        ids = {item["evidence_id"] for item in evidence}
        assert all(set(f["evidence_ids"]) <= ids for f in detail["report"]["findings"])
        assert all(item["redacted"] for item in evidence)
        feedback = client.post(
            f"/api/v1/diagnoses/{task_id}/feedback", headers=headers, json={"verdict": "correct"}
        )
        assert feedback.status_code == 201
    engine = create_engine(settings.postgres_url())
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM audit_events WHERE task_id=:id"), {"id": task_id}
                ).scalar()
                == 5
            )
    finally:
        engine.dispose()
    return detail, evidence


def test_live_no_lock(lab):
    detail, evidence = diagnose(lab[0], "lock_wait")
    assert detail["task"]["status"] == "completed"
    assert next(e for e in evidence if e["source"] == "mysql.lock_chain")["payload"]["edges"] == []


def test_live_mcp_evidence_and_audit(lab):
    settings, _ = lab
    headers = {
        "Authorization": "Bearer " + settings.api_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
    }
    config = settings.model_copy(update={"mcp_enabled": True})
    with TestClient(create_app(config), base_url="http://127.0.0.1:8086") as client:
        result = client.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "mysql_health", "arguments": {"instance_id": "mysql-lab-01"}},
            },
        ).json()["result"]
        assert not result["isError"]
        item = result["structuredContent"]
        assert item["status"] == "ok"
    with TestClient(create_app(settings)) as client:
        persisted = client.get(
            f"/api/v1/diagnoses/{item['task_id']}/evidence", headers=headers
        ).json()
        assert persisted[0]["evidence_id"] == item["evidence_id"]
    engine = create_engine(settings.postgres_url())
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT actor, action, target FROM audit_events WHERE task_id=:id"),
                {"id": item["task_id"]},
            ).all()
            assert ("mcp-local-user", "tool.executed", "mysql.health") in rows
            assert len(rows) == 3
    finally:
        engine.dispose()


def test_live_agent_report_survives_reload(lab, monkeypatch):
    import json

    import httpx

    from dbic.adapters.agent.compatible import CompatibleAgentRuntime
    from tests.unit.test_agent import completion, suggestion

    settings, _ = lab
    config = settings.model_copy(
        update={
            "agent_backend": "openai_compatible",
            "llm_base_url": "http://localhost:12345/v1",
            "llm_model": "fixture",
        }
    )
    from pydantic import SecretStr

    config = config.model_copy(update={"llm_api_key": SecretStr("fixture-model-key")})

    def respond(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(
            200, json=completion([suggestion(context["evidence"][0]["evidence_id"])])
        )

    monkeypatch.setattr(
        "dbic.main.agent_runtime",
        lambda _: CompatibleAgentRuntime(config, transport=httpx.MockTransport(respond)),
    )
    detail, _ = diagnose(config, "lock_wait")
    headers = {"Authorization": "Bearer " + settings.api_token.get_secret_value()}
    with TestClient(create_app(settings)) as client:
        persisted = client.get(
            f"/api/v1/diagnoses/{detail['task']['task_id']}", headers=headers
        ).json()
        assert persisted["report"]["agent_status"] == "ok"
        assert persisted["report"]["agent_suggestions"] == detail["report"]["agent_suggestions"]


def test_live_blocking_lock(lab):
    settings, root_options = lab
    blocker = pymysql.connect(**(root_options | {"autocommit": False}))
    try:
        waiter = pymysql.connect(**(root_options | {"autocommit": False}))
    except Exception:
        blocker.close()
        raise
    errors = []

    def wait_for_lock():
        try:
            with waiter.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout=12")
                cursor.execute("UPDATE dbic_fixture.accounts SET value=value+1 WHERE id=1")
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            waiter.rollback()

    thread = threading.Thread(target=wait_for_lock)
    try:
        with blocker.cursor() as cursor:
            cursor.execute("UPDATE dbic_fixture.accounts SET value=value+1 WHERE id=1")
        thread.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with pymysql.connect(**root_options) as conn, conn.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM performance_schema.data_lock_waits")
                if cursor.fetchone()[0]:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("fixture did not produce a lock wait")
        detail, evidence = diagnose(settings, "lock_wait")
        assert detail["task"]["status"] == "completed"
        edges = next(e for e in evidence if e["source"] == "mysql.lock_chain")["payload"]["edges"]
        assert any(
            e["blocker_session"] == blocker.thread_id()
            and e["waiter_session"] == waiter.thread_id()
            for e in edges
        )
    finally:
        blocker.rollback()
        if thread.ident is not None:
            thread.join(timeout=15)
        blocker.close()
        waiter.close()
    assert not thread.is_alive()
    assert not errors, errors


def test_live_permission_denied(lab):
    settings, options = lab
    with pymysql.connect(**options) as conn, conn.cursor() as cursor:
        cursor.execute("REVOKE SELECT ON performance_schema.data_lock_waits FROM 'diag'@'%'")
        try:
            detail, evidence = diagnose(settings, "lock_wait")
            assert detail["task"]["status"] == "partial"
            assert not detail["report"]["findings"]
            assert any(e["status"] == "forbidden" for e in evidence)
        finally:
            cursor.execute("GRANT SELECT ON performance_schema.data_lock_waits TO 'diag'@'%'")


def test_live_digest_present(lab):
    settings, options = lab
    with (
        pymysql.connect(**(options | {"database": "dbic_fixture"})) as conn,
        conn.cursor() as cursor,
    ):
        cursor.execute("SELECT * FROM accounts WHERE id=1")
        cursor.fetchall()
    detail, evidence = diagnose(settings, "slow_query")
    assert detail["task"]["status"] == "partial"
    result = next(e for e in evidence if e["source"] == "mysql.slow_query_summary")
    assert result["status"] == "ok"
    assert result["payload"]["digests"]
    assert "SELECT" not in str(result["payload"])


def test_live_digest_collection_missing(lab):
    settings, options = lab
    with pymysql.connect(**options) as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT ENABLED FROM performance_schema.setup_consumers WHERE NAME='statements_digest'"
        )
        original = cursor.fetchone()[0]
        try:
            cursor.execute(
                "UPDATE performance_schema.setup_consumers SET ENABLED='NO' "
                "WHERE NAME='statements_digest'"
            )
            detail, evidence = diagnose(settings, "slow_query")
            assert detail["task"]["status"] == "partial"
            assert not detail["report"]["findings"]
            assert any(e["status"] == "unavailable" for e in evidence)
        finally:
            cursor.execute(
                "UPDATE performance_schema.setup_consumers SET ENABLED=%s "
                "WHERE NAME='statements_digest'",
                (original,),
            )


def test_live_account_cannot_read_business_data(lab):
    options = MySQLDiagnostics(lab[0].mysql_dsn()).options
    with pymysql.connect(**options) as conn, conn.cursor() as cursor:
        with pytest.raises(pymysql.err.OperationalError) as error:
            cursor.execute("SELECT * FROM dbic_fixture.accounts")
        assert error.value.args[0] == 1142


def test_live_atomic_claim_and_invalid_reference_rollback(lab):
    async def check():
        engine = create_engine(lab[0].postgres_url())
        store = PostgresTaskStore(engine)
        task = DiagnosticTask(
            instance_id="mysql-lab-01",
            database_type="mysql",
            requested_by="test",
            problem_type="lock_wait",
        )
        try:
            await store.create_task(task)
            running = transition_task(task, TaskStatus.RUNNING)
            claimed = await asyncio.gather(
                store.update_task(running, TaskStatus.QUEUED),
                store.update_task(running, TaskStatus.QUEUED),
            )
            assert sorted(claimed) == [False, True]
            terminal = transition_task(running, TaskStatus.FAILED)
            report = Report(
                task_id=task.task_id, status="inconclusive", summary="test", evidence_ids=[uuid4()]
            )
            with pytest.raises(ValueError, match="outside its persisted task"):
                await store.finish_task(terminal, report)
            detail = await store.get_task(task.task_id)
            assert detail.task.status == TaskStatus.RUNNING
            assert detail.report is None
            await store.finish_task(
                terminal,
                Report(task_id=task.task_id, status="inconclusive", summary="fixture finished"),
            )
            assert not await store.update_task(running, TaskStatus.QUEUED)
        finally:
            engine.dispose()

    asyncio.run(check())
