"""Only test-doris receives synthetic queries; application tools remain read-only."""

import asyncio
import os
import subprocess
import sys
import time

import httpx
import pymysql
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from dbic.main import create_app
from dbic.plugins.doris.diagnostics import DorisDiagnostics
from dbic.settings import Settings
from tests.unit.test_mcp import rpc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_DORIS_TESTS") != "1", reason="requires isolated test-doris"
    ),
]


@pytest.fixture(scope="module")
def doris_lab():
    from scripts.init_doris_lab import bootstrap

    bootstrap()
    config = Settings()
    config = Settings.model_validate(
        config.model_dump()
        | {
            "doris_enabled": True,
            "mcp_enabled": True,
            "database_url": config.postgres_url()
            .set(database="dbic_test")
            .render_as_string(hide_password=False),
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=os.environ
        | {"DBIC_DATABASE_URL": config.postgres_url().render_as_string(hide_password=False)},
        check=True,
    )
    return config


def tool(config, name):
    return asyncio.run(
        DorisDiagnostics(config.doris_diag_password).run(
            tool_id="doris." + name, instance_id="doris-lab-01", params={}
        )
    )


def test_real_health_report_feedback_and_mcp(doris_lab):
    config = doris_lab
    health = tool(config, "fe_be_health")
    assert health.status == "ok" and health.payload == {"online_backends": 1, "total_backends": 1}
    headers = {
        "Authorization": "Bearer " + config.api_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/v1/diagnoses",
            headers=headers,
            json={
                "instance_id": "doris-lab-01",
                "problem_type": "cluster_health",
            },
        )
        assert created.status_code == 202
        path = "/api/v1/diagnoses/" + created.json()["task_id"]
        detail = client.get(path, headers=headers).json()
        evidence = client.get(path + "/evidence", headers=headers).json()
        assert len(evidence) == len(detail["steps"]) == 2
        assert all(e["status"] in {"ok", "empty"} for e in evidence)
        assert detail["task"]["status"] == "partial"
        assert detail["report"]["agent_status"] == "disabled"
        assert all(
            f["evidence_ids"] and set(f["evidence_ids"]) <= {e["evidence_id"] for e in evidence}
            for f in detail["report"]["findings"]
        )
        assert (
            client.post(
                path + "/feedback", headers=headers, json={"verdict": "partial"}
            ).status_code
            == 201
        )
        for name in ("doris_fe_be_health", "doris_query_profile_summary"):
            result = rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": "doris-lab-01"}},
                headers=headers,
            ).json()["result"]
            assert not result.get("isError")
            item = result["structuredContent"]
            assert client.get(
                "/api/v1/diagnoses/" + item["task_id"] + "/evidence", headers=headers
            ).json() == [item]
    with TestClient(create_app(config)) as client:
        assert client.get(path + "/evidence", headers=headers).json() == evidence
        assert len(client.get(path, headers=headers).json()["feedback"]) == 1


def test_real_profile_and_minimal_permissions(doris_lab):
    config = doris_lab
    with (
        pymysql.connect(
            host="doris-fe",
            port=9030,
            user="diag",
            password=config.doris_diag_password.get_secret_value(),
            connect_timeout=2,
            read_timeout=8,
            write_timeout=2,
        ) as conn,
        conn.cursor() as cursor,
    ):
        cursor.execute("SHOW GRANTS")
        grants = str(cursor.fetchall()).upper()
        assert "ADMIN_PRIV" not in grants and "NODE_PRIV" not in grants
        cursor.execute("SET enable_profile=true")
        cursor.execute('SELECT SUM(number) FROM numbers("number"="10")')
        assert cursor.fetchone() == (45,)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = tool(config, "query_profile_summary")
        if result.status == "ok" and result.payload["samples"]:
            break
        time.sleep(0.2)
    else:
        pytest.fail("real diag Profile not visible: " + result.status)
    assert "SELECT" not in result.model_dump_json() and "number" not in str(result.payload)
    with httpx.Client(
        base_url="http://doris-fe:8030",
        trust_env=False,
        timeout=3,
        auth=("diag", config.doris_diag_password.get_secret_value()),
    ) as client:
        assert client.get("/api/fe_version_info").json()["code"] in {401, 403}
    denied = asyncio.run(
        DorisDiagnostics(SecretStr("wrong")).run(
            tool_id="doris.fe_be_health", instance_id="doris-lab-01", params={}
        )
    )
    assert denied.status == "forbidden" and not denied.payload
