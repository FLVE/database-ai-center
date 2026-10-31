import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from redis.exceptions import AuthenticationError, ConnectionError, NoPermissionError, ResponseError
from redis.exceptions import TimeoutError as RedisTimeout

from dbic.core.models import DiagnosticTask, Evidence, utc_now
from dbic.core.redis_models import MemorySnapshot
from dbic.core.redis_rules import redis_findings
from dbic.plugins.redis.audit import read_audit
from dbic.plugins.redis.diagnostics import RedisDiagnostics
from dbic.plugins.redis.models import memory_snapshot, slowlog_summary


def audit_data(now=None):
    now = now or utc_now()
    return dict(
        schema_version=1,
        instance_id="redis-lab-01",
        database_type="redis",
        environment="lab",
        audit_id=str(uuid4()),
        started_at=(now - timedelta(minutes=1)).isoformat(),
        finished_at=now.isoformat(),
        coverage="sampled",
        scanned_keys=100,
        bigkey_threshold_bytes=1000,
        bigkey_count=2,
        largest_key_bytes=2000,
    )


def task():
    return DiagnosticTask(
        requested_by="test",
        database_type="redis",
        instance_id="redis-lab-01",
        problem_type="memory_pressure",
    )


def item(target, source, initial_payload, **changes):
    return Evidence(
        task_id=target.task_id,
        instance_id=target.instance_id,
        source=source,
        kind="database_state",
        status="ok",
        quality="observed",
        redacted=True,
        observed_at=utc_now(),
        payload=initial_payload,
    ).model_copy(update=changes)


def test_memory_fields_missing_are_not_zero_and_raw_fields_dropped():
    result = memory_snapshot({"used_memory": 100, "secret": "do-not-copy"}, {})
    assert result.used_memory_bytes == 100 and result.maxmemory_bytes is None
    assert "secret" not in result.model_dump_json()


def test_redis_startup_accounting_underflow_is_a_gap_not_a_giant_dataset():
    result = memory_snapshot(
        {
            "used_memory": 1366792,
            "used_memory_overhead": 1370840,
            "used_memory_dataset": 18446744073709547568,
        },
        {},
    )
    assert result.used_memory_bytes == 1366792
    assert result.used_memory_dataset_bytes is None and result.used_memory_overhead_bytes is None


@pytest.mark.parametrize("value", [-1, True, "100", 1.2, 2**64])
def test_invalid_memory_counts_rejected(value):
    with pytest.raises(ValidationError):
        MemorySnapshot(used_memory_bytes=value)


def test_fragmentation_signed_difference_and_nonfinite_ratio():
    assert MemorySnapshot(mem_fragmentation_bytes=-10).mem_fragmentation_bytes == -10
    for value in (float("nan"), float("inf"), -1):
        with pytest.raises(ValidationError):
            MemorySnapshot(mem_fragmentation_ratio=value)


@pytest.mark.parametrize(
    "used,limit,expected",
    [
        (89, 100, "pressure_below_threshold"),
        (90, 100, "pressure_high"),
        (100, 0, "no_limit"),
        (100, None, None),
    ],
)
def test_memory_rule_boundaries(used, limit, expected):
    target = task()
    evidence = item(
        target,
        "redis.memory_breakdown",
        {"used_memory_bytes": used, "maxmemory_bytes": limit, "mem_not_counted_for_evict_bytes": 0},
    )
    findings = redis_findings(target, [evidence])
    assert findings
    assert all(
        f.evidence_ids == [evidence.evidence_id] and f.certainty == "observed" for f in findings
    )
    assert (
        any(f.rule_id == "redis.memory." + expected for f in findings)
        if expected
        else len(findings) == 1
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": uuid4()},
        {"instance_id": "mysql-lab-01"},
        {"redacted": False},
        {"status": "unavailable"},
        {"offset_seconds": -91},
        {"offset_seconds": 30},
        {"payload": {"used_memory_bytes": -1}},
    ],
)
def test_invalid_evidence_cannot_generate_facts(changes):
    target = task()
    now = utc_now()
    if "offset_seconds" in changes:
        changes = {"observed_at": now + timedelta(seconds=changes["offset_seconds"])}
    evidence = item(target, "redis.memory_breakdown", {"used_memory_bytes": 100}, **changes)
    assert not redis_findings(target, [evidence], now=now)


def test_audit_hypothesis_requires_two_owned_fresh_sources():
    target, now = task(), utc_now()
    data = audit_data(now - timedelta(minutes=5)) | {"origin": "imported_audit"}
    audit = item(target, "redis.bigkey_audit_read", data, observed_at=now - timedelta(minutes=5))
    memory = item(
        target,
        "redis.memory_breakdown",
        {"used_memory_bytes": 95, "maxmemory_bytes": 100, "mem_not_counted_for_evict_bytes": 0},
    )
    findings = redis_findings(target, [audit, memory], now=now)
    hypothesis = next(f for f in findings if f.certainty == "supported_hypothesis")
    assert set(hypothesis.evidence_ids) == {audit.evidence_id, memory.evidence_id}
    assert not any(f.certainty == "confirmed_root_cause" for f in findings)
    assert not any(f.certainty == "supported_hypothesis" for f in redis_findings(target, [audit]))


def test_slowlog_drops_arguments_and_client_metadata():
    now = utc_now()
    rows = [
        [
            1,
            int(now.timestamp()),
            20,
            [b"SET", b"secret-key", b"secret-value"],
            b"private-ip",
            b"private-name",
        ],
        [2, int(now.timestamp()), 40, [b"private-command"], b"private-ip", b""],
    ]
    result = slowlog_summary(rows, now)
    assert result.sample_count == 2 and result.total_duration_us == 60
    assert {x.command for x in result.commands} == {"SET", "OTHER"}
    assert "secret" not in result.model_dump_json() and "private" not in result.model_dump_json()
    assert slowlog_summary([], now).sample_count == 0


@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "stale",
        "future",
        "wrong_instance",
        "extra_key",
        "oversize",
        "bad_json",
        "symlink",
        "parent_symlink",
        "directory",
        "wrong_version",
        "invalid_count",
    ],
)
def test_audit_file_boundary(tmp_path, scenario):
    now = utc_now()
    directory = tmp_path / "audit"
    directory.mkdir()
    path = directory / "redis-lab-01.json"
    data = audit_data(now)
    if scenario == "stale":
        data = audit_data(now - timedelta(minutes=16))
    elif scenario == "future":
        data = audit_data(now + timedelta(seconds=10))
    elif scenario == "wrong_instance":
        data["instance_id"] = "other"
    elif scenario == "extra_key":
        data["key"] = "sensitive"
    elif scenario == "wrong_version":
        data["schema_version"] = 2
    elif scenario == "invalid_count":
        data["bigkey_count"] = 101
    path.write_text(json.dumps(data))
    if scenario == "oversize":
        path.write_text(" " * 65537)
    elif scenario == "bad_json":
        path.write_text("{")
    elif scenario == "symlink":
        other = tmp_path / "other.json"
        path.rename(other)
        path.symlink_to(other)
    elif scenario == "parent_symlink":
        link = tmp_path / "link"
        link.symlink_to(directory, target_is_directory=True)
        directory = link
    elif scenario == "directory":
        path.unlink()
        path.mkdir()
    if scenario == "valid":
        assert read_audit(directory, now).bigkey_count == 2
    else:
        with pytest.raises((ValueError, OSError, LookupError)):
            read_audit(directory, now)


@pytest.mark.parametrize(
    "error,status",
    [
        (AuthenticationError, "forbidden"),
        (NoPermissionError, "forbidden"),
        (RedisTimeout, "timeout"),
        (ConnectionError, "unavailable"),
        (ResponseError, "error"),
    ],
)
def test_tool_failure_is_sanitized_without_retry(monkeypatch, caplog, error, status):
    class Client:
        calls = 0
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def info(self, section):
            self.calls += 1
            raise error("private-password")

    client = Client()
    monkeypatch.setattr("dbic.plugins.redis.diagnostics.Redis", lambda **kw: client)
    result = asyncio.run(
        RedisDiagnostics(SecretStr("test-only")).run(
            tool_id="redis.memory_breakdown", instance_id="redis-lab-01", params={}
        )
    )
    assert result.status == status and client.calls == 1 and client.closed
    assert "private-password" not in result.model_dump_json() + caplog.text


def test_no_credentials_missing_audit_and_wrong_target(tmp_path):
    async def check():
        tools = RedisDiagnostics(None, tmp_path)
        for tool_id in ("redis.memory_breakdown", "redis.bigkey_audit_read"):
            result = await tools.run(tool_id=tool_id, instance_id="redis-lab-01", params={})
            assert result.status == "unavailable"
        for tool_id, instance, params in [
            ("redis.memory_breakdown", "mysql-lab-01", {}),
            ("redis.execute", "redis-lab-01", {}),
            ("redis.slowlog_summary", "redis-lab-01", {"command": "KEYS *"}),
        ]:
            assert (
                await tools.run(tool_id=tool_id, instance_id=instance, params=params)
            ).status == "forbidden"

    asyncio.run(check())


@pytest.mark.parametrize(
    "version,mode", [("7.4.1", "standalone"), ("8.4.8", "cluster"), ("8.4.invalid", "standalone")]
)
def test_unsupported_server_never_runs_data_commands(monkeypatch, version, mode):
    class Client:
        calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def info(self, section):
            self.calls.append(section)
            return {"redis_version": version, "redis_mode": mode}

    client = Client()
    monkeypatch.setattr("dbic.plugins.redis.diagnostics.Redis", lambda **kw: client)
    result = asyncio.run(
        RedisDiagnostics(SecretStr("test-only")).run(
            tool_id="redis.memory_breakdown", instance_id="redis-lab-01", params={}
        )
    )
    assert result.status == "unavailable" and client.calls == ["server"]


def test_cancelled_read_closes_connection(monkeypatch):
    class Client:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def info(self, section):
            await asyncio.sleep(10)

    client = Client()
    monkeypatch.setattr("dbic.plugins.redis.diagnostics.Redis", lambda **kw: client)

    async def check():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                RedisDiagnostics(SecretStr("test-only")).run(
                    tool_id="redis.memory_breakdown", instance_id="redis-lab-01", params={}
                ),
                timeout=0.01,
            )

    asyncio.run(check())
    assert client.closed


def test_empty_audit_setting_is_disabled():
    from dbic.settings import Settings
    from tests.unit.test_api import settings

    assert (
        Settings.model_validate(settings().model_dump() | {"redis_audit_dir": ""}).redis_audit_dir
        is None
    )
