import asyncio
from datetime import timedelta

import pytest
from pydantic import SecretStr, ValidationError
from pymongo.errors import AutoReconnect, ExecutionTimeout, OperationFailure

from dbic.core.evidence import normalize
from dbic.core.models import DiagnosticTask, ToolResult, utc_now
from dbic.core.mongodb_rules import mongodb_findings
from dbic.plugins.mongodb import diagnostics as module
from dbic.plugins.mongodb.diagnostics import MongoDBDiagnostics
from dbic.plugins.mongodb.models import current_operations, replica_snapshot


def replica_raw():
    return {
        "set": "dbic-rs",
        "myState": 1,
        "members": [
            {
                "_id": 0,
                "state": 1,
                "health": 1.0,
                "self": True,
                "name": "private-host",
                "errmsg": "private-data",
            }
        ],
    }


def test_projection_redaction_and_truncation():
    raw = {
        "op": "getmore",
        "secs_running": 2,
        "waitingForLock": False,
        "command": {"find": "secret-collection"},
        "client": "private-address",
    }
    summary = current_operations([raw] * 51)
    assert summary.truncated and len(summary.samples) == 50
    assert "secret" not in summary.model_dump_json() and "private" not in summary.model_dump_json()
    assert current_operations([{"op": "untrusted-command"}]).samples[0].operation == "OTHER"
    assert current_operations([{}]).samples[0].running_seconds is None
    assert "private" not in replica_snapshot(replica_raw()).model_dump_json()
    with pytest.raises(ValueError):
        current_operations([raw] * 52)


@pytest.mark.parametrize(
    "field,value",
    [
        ("secs_running", -1),
        ("secs_running", True),
        ("secs_running", "2"),
        ("secs_running", float("nan")),
        ("waitingForLock", "false"),
        ("waitingForLock", 0),
    ],
)
def test_invalid_operation_fields(field, value):
    with pytest.raises(ValidationError):
        current_operations([{field: value}])


@pytest.mark.parametrize(
    "change",
    [
        {"set": "other"},
        {"members": []},
        {"members": [{}, {}]},
        {"myState": True},
        {"myState": 11},
        {"myState": 2},
    ],
)
def test_invalid_replica_topology_or_state(change):
    with pytest.raises((LookupError, ValueError)):
        replica_snapshot(replica_raw() | change)


def evidence_for(task, source, payload, status="ok"):
    return normalize(
        task,
        ToolResult(
            source=source, payload=payload, status=status, observed_at=utc_now(), duration_ms=1
        ),
    )


def test_rule_facts_missing_fields_and_unhealthy_state():
    task = DiagnosticTask(
        instance_id="mongodb-lab-01",
        database_type="mongodb",
        problem_type="activity_health",
        requested_by="unit-test",
    )
    sample = current_operations([{"op": "getmore", "secs_running": 5}]).model_dump(mode="json")
    item = evidence_for(task, "mongodb.current_ops_summary", sample)
    findings = mongodb_findings(task, [item])
    assert len(findings) == 1 and findings[0].evidence_ids == [item.evidence_id]
    assert findings[0].certainty == "observed" and "不单独证明慢查询" in findings[0].statement
    raw = replica_raw()
    raw["myState"] = raw["members"][0]["state"] = 2
    replica = evidence_for(
        task, "mongodb.replica_health", replica_snapshot(raw).model_dump(mode="json")
    )
    assert mongodb_findings(task, [replica])[0].rule_id.endswith("primary_not_observed")


@pytest.mark.parametrize(
    "change",
    [
        {"instance_id": "mysql-lab-01"},
        {"redacted": False},
        {"status": "unavailable"},
        {"kind": "log"},
        {"quality": "inferred"},
        {"payload": {"unexpected": "private"}},
        {"status": "ok"},
    ],
)
def test_invalid_evidence_cannot_become_a_finding(change):
    task = DiagnosticTask(
        instance_id="mongodb-lab-01",
        database_type="mongodb",
        problem_type="activity_health",
        requested_by="unit-test",
    )
    item = evidence_for(
        task, "mongodb.current_ops_summary", {"samples": [], "truncated": False}, "empty"
    )
    assert not mongodb_findings(task, [item.model_copy(update=change)])
    assert not mongodb_findings(task.model_copy(update={"database_type": "mysql"}), [item])
    assert not mongodb_findings(task, [item], now=item.observed_at + timedelta(seconds=91))
    assert not mongodb_findings(task, [item], now=item.observed_at - timedelta(seconds=6))


class Cursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def to_list(self, length):
        assert length == 51
        return []


class Client:
    calls = []
    failure = None
    version = "8.0.34"
    hosts = ["mongodb-lab:27017"]

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, **kwargs):
        assert kwargs["host"] == "mongodb-lab" and kwargs["directConnection"]
        assert not kwargs["retryReads"] and not kwargs["retryWrites"]
        self.admin = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.calls.append("closed")

    async def command(self, command):
        self.calls.append(command)
        if self.failure:
            raise self.failure
        if command == "buildInfo":
            return {"version": self.version}
        if command == "hello":
            return {"setName": "dbic-rs", "hosts": self.hosts}
        return replica_raw()

    async def aggregate(self, pipeline, **kwargs):
        assert pipeline[-1] == {"$limit": 51}
        assert pipeline[-2] == {
            "$project": {"_id": 0, "op": 1, "secs_running": 1, "waitingForLock": 1}
        }
        assert kwargs == {"maxTimeMS": 1500, "allowDiskUse": False, "batchSize": 51}
        return Cursor()


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(module, "AsyncMongoClient", Client)
    monkeypatch.setattr(Client, "calls", [])
    return Client


def run(
    tool="mongodb.current_ops_summary", instance="mongodb-lab-01", params=None, password="fake"
):
    return asyncio.run(
        MongoDBDiagnostics(SecretStr(password) if password else None).run(
            tool_id=tool, instance_id=instance, params=params or {}
        )
    )


def test_exact_queries_and_close(fake_client):
    assert run().status == "empty"
    assert run("mongodb.replica_health").status == "ok"
    assert fake_client.calls.count("closed") == 2


@pytest.mark.parametrize(
    "failure,status",
    [
        (OperationFailure("secret", 13), "forbidden"),
        (OperationFailure("secret", 18), "forbidden"),
        (OperationFailure("secret", 94), "unavailable"),
        (OperationFailure("secret", 999), "error"),
        (ExecutionTimeout("secret", 50), "timeout"),
        (AutoReconnect("secret"), "unavailable"),
        (TimeoutError("secret"), "timeout"),
        (ValueError("secret"), "error"),
    ],
)
def test_failure_mapping_redacts_exception(fake_client, monkeypatch, failure, status, caplog):
    monkeypatch.setattr(fake_client, "failure", failure)
    result = run()
    assert result.status == status and "secret" not in result.model_dump_json() + caplog.text
    assert fake_client.calls[-1] == "closed"


def test_wrong_target_missing_credentials_and_versions_never_probe(fake_client, monkeypatch):
    assert run(instance="production").status == "forbidden"
    assert run(tool="mongodb.killop").status == "forbidden"
    assert run(params={"command": "dropDatabase"}).status == "forbidden"
    assert run(password=None).status == "unavailable"
    assert not fake_client.calls
    monkeypatch.setattr(fake_client, "version", "9.0.0")
    assert run().status == "unavailable"
    monkeypatch.setattr(fake_client, "version", "8.0.34")
    monkeypatch.setattr(fake_client, "hosts", ["production:27017"])
    assert run().status == "unavailable"


def test_total_timeout_cancels_and_closes_client(fake_client, monkeypatch):
    original_timeout = asyncio.timeout

    async def stalled(self, command):
        await asyncio.sleep(1)

    monkeypatch.setattr(fake_client, "command", stalled)
    monkeypatch.setattr(module.asyncio, "timeout", lambda _: original_timeout(0.02))
    assert run().status == "timeout"
    assert fake_client.calls == ["closed"]
