import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from pydantic import SecretStr, ValidationError

from dbic.core.evidence import normalize
from dbic.core.models import DiagnosticTask, ToolResult, utc_now
from dbic.core.postgresql_models import BlockingSnapshot, VacuumSnapshot
from dbic.core.postgresql_rules import postgresql_findings
from dbic.plugins.postgresql import queries
from dbic.plugins.postgresql.diagnostics import PostgreSQLDiagnostics


def vacuum_table():
    return {
        "relation_id": 123,
        "live_tuples_estimate": 2000,
        "dead_tuples_estimate": 2000,
        "last_vacuum_at": None,
        "last_autovacuum_at": None,
        "vacuum_count": 0,
        "autovacuum_count": 0,
    }


def payload(source):
    if source == "postgresql.blocking_sessions":
        return {
            "sessions": [{"waiter_pid": 12, "blocker_pids": [11], "transaction_age_seconds": 2.0}],
            "truncated": False,
        }
    return {"tables": [vacuum_table()], "truncated": False, "stats_reset_at": None}


def test_rules_and_evidence_boundaries():
    task = DiagnosticTask(
        instance_id="postgresql-lab-01",
        database_type="postgresql",
        problem_type="maintenance_health",
        requested_by="test",
    )
    for source, rule in [
        ("postgresql.blocking_sessions", "blocking.observed"),
        ("postgresql.vacuum_health", "vacuum.review_candidates"),
    ]:
        item = normalize(
            task,
            ToolResult(
                source=source,
                status="ok",
                observed_at=utc_now(),
                duration_ms=1,
                payload=payload(source),
            ),
        )
        findings = postgresql_findings(task, [item])
        assert len(findings) == 1 and findings[0].rule_id == "postgresql." + rule
        assert (
            findings[0].evidence_ids == [item.evidence_id] and findings[0].certainty == "observed"
        )
        for changes in (
            {"redacted": False},
            {"quality": "inferred"},
            {"instance_id": "production"},
            {"status": "forbidden"},
            {"payload": {"private_sql": "bad"}},
        ):
            assert not postgresql_findings(task, [item.model_copy(update=changes)])
        assert not postgresql_findings(task, [item], now=item.observed_at + timedelta(seconds=91))
        assert not postgresql_findings(task, [item], now=item.observed_at - timedelta(seconds=6))


@pytest.mark.parametrize(
    "change",
    [
        {"live_tuples_estimate": -1},
        {"relation_id": True},
        {"dead_tuples_estimate": "3"},
        {"vacuum_count": None},
        {"sql": "private"},
    ],
)
def test_invalid_statistics_rejected(change):
    with pytest.raises(ValidationError):
        VacuumSnapshot.model_validate(
            {"tables": [vacuum_table() | change], "truncated": False, "stats_reset_at": None}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"waiter_pid": 0},
        {"blocker_pids": []},
        {"transaction_age_seconds": float("nan")},
        {"blocker_pids": [-1]},
    ],
)
def test_invalid_blocking_rejected(change):
    with pytest.raises(ValidationError):
        BlockingSnapshot.model_validate(
            {
                "sessions": [payload("postgresql.blocking_sessions")["sessions"][0] | change],
                "truncated": False,
            }
        )


def run(
    tool="postgresql.blocking_sessions", instance="postgresql-lab-01", params=None, password="fake"
):
    return asyncio.run(
        PostgreSQLDiagnostics(SecretStr(password) if password else None).run(
            tool_id=tool, instance_id=instance, params=params or {}
        )
    )


@pytest.fixture
def connection(monkeypatch):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    check = MagicMock()
    check.fetchone = AsyncMock(
        return_value={"version": 170011, "database": "dbic_lab", "allowed": True, "tracked": True}
    )
    data = MagicMock()
    data.fetchall = AsyncMock(return_value=[])
    reset = MagicMock()
    reset.fetchone = AsyncMock(return_value={"stats_reset": None})
    conn.execute = AsyncMock(side_effect=[check, data, reset])
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    return conn, connect, check, data


def test_queries_limits_and_readonly(connection):
    conn, connect, _, _ = connection
    assert run().status == "empty"
    options = connect.call_args.kwargs
    assert options["host"] == "postgresql-lab" and options["dbname"] == "dbic_lab"
    assert "default_transaction_read_only=on" in options["options"]
    assert "statement_timeout=1500" in options["options"]
    assert [c.args[0] for c in conn.execute.call_args_list] == [queries.CHECK, queries.BLOCKING]
    assert conn.__aexit__.await_count == 1


def test_truncated_vacuum_and_no_sensitive_columns(connection):
    conn, _, _, data = connection
    data.fetchall.return_value = [vacuum_table()] * 51
    result = run("postgresql.vacuum_health")
    assert (
        result.status == "ok"
        and len(result.payload["tables"]) == 50
        and result.payload["truncated"]
    )
    assert "query" not in queries.VACUUM and "relname" not in queries.VACUUM


@pytest.mark.parametrize(
    "check,status",
    [
        ({"allowed": False}, "forbidden"),
        ({"version": 180000}, "unavailable"),
        ({"tracked": False}, "unavailable"),
    ],
)
def test_missing_visibility_never_looks_healthy(connection, check, status):
    connection[2].fetchone.return_value |= check
    assert run("postgresql.vacuum_health").status == status


@pytest.mark.parametrize(
    "error,status",
    [
        (psycopg.errors.InsufficientPrivilege("private"), "forbidden"),
        (psycopg.errors.QueryCanceled("private"), "timeout"),
        (psycopg.OperationalError("private"), "unavailable"),
        (TimeoutError("private"), "timeout"),
        (ValueError("private"), "error"),
    ],
)
def test_failure_redaction(connection, error, status, caplog):
    connection[0].execute.side_effect = error
    result = run()
    assert result.status == status and "private" not in result.model_dump_json() + caplog.text


def test_unauthorized_requests_do_not_connect(connection):
    assert run(instance="dbic-postgres").status == "forbidden"
    assert run(params={"sql": "VACUUM"}).status == "forbidden"
    assert run(tool="postgresql.kill").status == "forbidden"
    assert run(password=None).status == "unavailable"
    connection[1].assert_not_called()
