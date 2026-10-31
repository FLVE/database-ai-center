"""Mutations only target the separate test-mongodb service on the verification network."""

import asyncio
import os
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import CursorType, MongoClient
from pymongo.errors import OperationFailure
from sqlalchemy import create_engine, text

from dbic.main import create_app
from dbic.plugins.mongodb.diagnostics import MongoDBDiagnostics
from dbic.settings import Settings
from tests.unit.test_mcp import rpc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_MONGODB_TESTS") != "1",
        reason="requires isolated test-mongodb and PostgreSQL",
    ),
]


@pytest.fixture(scope="module")
def mongo_lab():
    config = Settings()
    config = Settings.model_validate(
        config.model_dump()
        | {
            "mongodb_enabled": True,
            "mcp_enabled": True,
            "database_url": config.postgres_url()
            .set(database="dbic_test")
            .render_as_string(hide_password=False),
        }
    )
    env = os.environ | {
        "DBIC_DATABASE_URL": config.postgres_url().render_as_string(hide_password=False)
    }
    for _ in range(2):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    admin = MongoClient(
        host="mongodb-lab",
        username=os.environ["MONGO_INITDB_ROOT_USERNAME"],
        password=os.environ["MONGO_INITDB_ROOT_PASSWORD"],
        authSource="admin",
        directConnection=True,
        serverSelectionTimeoutMS=2000,
        socketTimeoutMS=12000,
        appname="dbic-test-fixture",
    )
    assert admin.admin.command("buildInfo")["version"] == "8.0.34"
    assert admin.admin.command("hello")["isWritablePrimary"]
    yield config, admin
    admin.close()


def tool(config, name):
    return asyncio.run(
        MongoDBDiagnostics(config.mongodb_diag_password).run(
            tool_id="mongodb." + name, instance_id="mongodb-lab-01", params={}
        )
    )


def diagnose(config):
    headers = {"Authorization": "Bearer " + config.api_token.get_secret_value()}
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/v1/diagnoses",
            headers=headers,
            json={"instance_id": "mongodb-lab-01", "problem_type": "activity_health"},
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
    assert len(detail["steps"]) == len(evidence) == 2
    assert all(
        set(f["evidence_ids"]) <= {e["evidence_id"] for e in evidence}
        for f in detail["report"]["findings"]
    )
    engine = create_engine(config.postgres_url())
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE task_id=:id"),
                    {"id": created.json()["task_id"]},
                ).scalar()
                >= 3
            )
    finally:
        engine.dispose()
    return detail, evidence


def test_idle_replica_persistence_and_real_mcp(mongo_lab):
    config, _ = mongo_lab
    detail, evidence = diagnose(config)
    assert {e["status"] for e in evidence} <= {"ok", "empty"}
    assert any(
        f["rule_id"] == "mongodb.replica.primary_observed" for f in detail["report"]["findings"]
    )
    headers = {
        "Authorization": "Bearer " + config.api_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        for name in ("mongodb_current_ops_summary", "mongodb_replica_health"):
            result = rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": "mongodb-lab-01"}},
                headers=headers,
            ).json()["result"]
            assert not result.get("isError"), result
            item = result["structuredContent"]
            assert client.get(
                "/api/v1/diagnoses/" + item["task_id"] + "/evidence", headers=headers
            ).json() == [item]


def test_waiting_getmore_is_sampled_without_sensitive_payload(mongo_lab):
    config, admin = mongo_lab
    name = "dbic_fixture_" + uuid4().hex
    collection = admin.dbic_fixture.create_collection(name, capped=True, size=65536)
    cursor = None
    worker = None
    failures = []
    try:
        collection.insert_one({"synthetic_secret": "do-not-persist"})
        cursor = collection.find(
            cursor_type=CursorType.TAILABLE_AWAIT, batch_size=1
        ).max_await_time_ms(8000)
        next(cursor)

        def wait_for_record():
            try:
                next(cursor)
            except StopIteration:
                return
            except Exception as exc:
                failures.append(type(exc).__name__)

        worker = threading.Thread(target=wait_for_record)
        worker.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = tool(config, "current_ops_summary")
            if result.status == "ok" and any(
                s["operation"] == "getmore" for s in result.payload["samples"]
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("real getMore was not observed within fixture budget")
        _, evidence = diagnose(config)
        encoded = str(evidence) + result.model_dump_json()
        assert (
            name not in encoded
            and "do-not-persist" not in encoded
            and "synthetic_secret" not in encoded
        )
    finally:
        if worker:
            worker.join(timeout=10)
        if cursor:
            cursor.close()
        collection.drop()
    assert not failures and (not worker or not worker.is_alive())


def test_minimal_privileges_and_denial_recovery(mongo_lab):
    config, admin = mongo_lab
    role = admin.admin.command(
        {"rolesInfo": {"role": "dbicObserver", "db": "admin"}, "showPrivileges": True}
    )["roles"][0]
    assert role["roles"] == []
    assert role["privileges"] == [
        {"resource": {"cluster": True}, "actions": ["inprog", "replSetGetStatus"]}
    ]
    with MongoClient(
        host="mongodb-lab",
        username="diag",
        password=config.mongodb_diag_password.get_secret_value(),
        authSource="admin",
        directConnection=True,
    ) as diag:
        with pytest.raises(OperationFailure) as denied:
            list(diag.dbic_fixture.nonexistent.find({}).limit(1))
        assert denied.value.code == 13
    # Role inspection proves mutation privileges absent without using destructive probes.
    admin.admin.command(
        {
            "updateRole": "dbicObserver",
            "privileges": [{"resource": {"cluster": True}, "actions": ["replSetGetStatus"]}],
        }
    )
    try:
        assert tool(config, "current_ops_summary").status == "forbidden"
        _, evidence = diagnose(config)
        assert (
            next(e for e in evidence if e["source"] == "mongodb.current_ops_summary")["status"]
            == "forbidden"
        )
        assert (
            next(e for e in evidence if e["source"] == "mongodb.replica_health")["status"] == "ok"
        )
    finally:
        admin.admin.command({"updateRole": "dbicObserver", "privileges": role["privileges"]})
    assert tool(config, "current_ops_summary").status in {"ok", "empty"}
