"""Redis mutations are confined to the disposable Compose verification network."""

import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text

from dbic.main import create_app
from dbic.plugins.redis.diagnostics import RedisDiagnostics
from dbic.settings import Settings
from tests.unit.test_mcp import rpc
from tests.unit.test_redis import audit_data

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_REDIS_TESTS") != "1",
        reason="requires isolated test-redis and PostgreSQL",
    ),
]


@pytest.fixture(scope="module")
def redis_lab():
    config = Settings().model_copy(update={"redis_enabled": True, "mcp_enabled": True})
    config = config.model_copy(
        update={
            "database_url": config.postgres_url()
            .set(database="dbic_test")
            .render_as_string(hide_password=False)
        }
    )
    # Construct through Settings so SecretStr and lab-only DSN validation remain active.
    config = Settings.model_validate(config.model_dump())
    env = os.environ | {
        "DBIC_DATABASE_URL": config.postgres_url().render_as_string(hide_password=False)
    }
    for _ in range(2):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    admin = Redis(
        host="redis-lab",
        username="fixture",
        password=os.environ["DBIC_TEST_REDIS_ADMIN_PASSWORD"],
        protocol=2,
        driver_info=None,
        socket_timeout=3,
        decode_responses=True,
    )
    assert admin.info("server")["redis_version"] == "8.4.8"
    yield config, admin
    admin.close()


def diagnose(config):
    headers = {"Authorization": "Bearer " + config.api_token.get_secret_value()}
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/v1/diagnoses",
            headers=headers,
            json={"instance_id": "redis-lab-01", "problem_type": "memory_pressure"},
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
    assert len(detail["steps"]) == len(evidence) == 3
    ids = {e["evidence_id"] for e in evidence}
    assert all(set(f["evidence_ids"]) <= ids for f in detail["report"]["findings"])
    engine = create_engine(config.postgres_url())
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE task_id=:id"),
                    {"id": created.json()["task_id"]},
                ).scalar()
                >= 4
            )
    finally:
        engine.dispose()
    return detail, evidence


def test_low_and_high_pressure_real_snapshots(redis_lab):
    config, admin = redis_lab
    low, _ = diagnose(config)
    assert any(
        f["rule_id"] == "redis.memory.pressure_below_threshold" for f in low["report"]["findings"]
    )
    prefix = "dbic-fixture:" + str(uuid4()) + ":"
    keys = []
    try:
        for i in range(100):
            memory = admin.info("memory")
            ratio = (memory["used_memory"] - memory["mem_not_counted_for_evict"]) / memory[
                "maxmemory"
            ]
            if ratio >= 0.92:
                break
            key = prefix + str(i)
            admin.set(key, "x" * 200000, ex=120)
            keys.append(key)
        assert ratio >= 0.92
        high, evidence = diagnose(config)
        assert any(f["rule_id"] == "redis.memory.pressure_high" for f in high["report"]["findings"])
        memory = next(e for e in evidence if e["source"] == "redis.memory_breakdown")
        assert memory["payload"]["maxmemory_bytes"] == admin.info("memory")["maxmemory"]
        assert prefix not in json.dumps(evidence)
    finally:
        if keys:
            admin.delete(*keys)


def test_exact_acl_and_slowlog_redaction(redis_lab):
    config, admin = redis_lab
    denied_before = sum(entry["count"] for entry in admin.acl_log(128))
    for command in [("PING",), ("INFO", "memory"), ("SLOWLOG", "GET", 20)]:
        assert admin.execute_command("ACL DRYRUN", "diag", *command) == "OK"
    for command in [
        ("SLOWLOG", "RESET"),
        ("KEYS", "*"),
        ("SCAN", 0),
        ("SET", "foo", "bar"),
        ("GET", "foo"),
        ("CONFIG", "SET", "maxmemory", 0),
        ("FLUSHALL",),
        ("MONITOR",),
        ("EVAL", "return 1", 0),
    ]:
        assert admin.execute_command("ACL DRYRUN", "diag", *command) != "OK"
    admin.ping()
    result = asyncio.run(
        RedisDiagnostics(config.redis_diag_password).run(
            tool_id="redis.slowlog_summary", instance_id="redis-lab-01", params={}
        )
    )
    assert result.status == "ok" and result.payload["sample_count"] > 0
    encoded = result.model_dump_json()
    assert (
        "client_address" not in encoded
        and "command_args" not in encoded
        and "dbic-fixture" not in encoded
    )
    assert sum(entry["count"] for entry in admin.acl_log(128)) == denied_before


def test_permission_failure_then_recovery(redis_lab):
    config, admin = redis_lab
    admin.execute_command("ACL SETUSER", "diag", "-slowlog|get")
    try:
        detail, evidence = diagnose(config)
        assert detail["task"]["status"] == "partial"
        assert (
            next(e for e in evidence if e["source"] == "redis.slowlog_summary")["status"]
            == "forbidden"
        )
    finally:
        admin.execute_command("ACL SETUSER", "diag", "+slowlog|get")


def test_imported_audit_and_real_mcp_persistence(redis_lab, tmp_path):
    config, _ = redis_lab
    (tmp_path / "redis-lab-01.json").write_text(json.dumps(audit_data()))
    config = config.model_copy(update={"redis_audit_dir": tmp_path})
    _, evidence = diagnose(config)
    audit = next(e for e in evidence if e["source"] == "redis.bigkey_audit_read")
    assert audit["status"] == "ok" and audit["payload"]["origin"] == "imported_audit"
    headers = {
        "Authorization": "Bearer " + config.api_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        for name in ("redis_memory_breakdown", "redis_slowlog_summary", "redis_bigkey_audit_read"):
            result = rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": "redis-lab-01"}},
                headers=headers,
            ).json()["result"]
            assert not result.get("isError"), result
            item = result["structuredContent"]
            assert client.get(
                "/api/v1/diagnoses/" + item["task_id"] + "/evidence", headers=headers
            ).json() == [item]
