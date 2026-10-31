import asyncio
from unittest.mock import MagicMock, patch

import pymysql
import pytest

from dbic.plugins.mysql import queries
from dbic.plugins.mysql.diagnostics import MySQLDiagnostics, connection_options, has_cycle

DSN = "mysql+pymysql://diag:test-only@mysql-lab:3306/performance_schema"


def run_tool(tool, *, version=None, rows=(), one=None, error=None):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [version or {"version": "8.4.0", "enabled": 1}, one]
    cursor.fetchmany.side_effect = rows
    if error:
        cursor.execute.side_effect = error
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    with patch("dbic.plugins.mysql.diagnostics.pymysql.connect", return_value=connection):
        result = asyncio.run(
            MySQLDiagnostics(DSN).run(tool_id=tool, instance_id="mysql-lab-01", params={})
        )
    return result, cursor


def test_health_units():
    result, cursor = run_tool(
        "mysql.health",
        rows=[
            [
                {"VARIABLE_NAME": name, "VARIABLE_VALUE": value}
                for name, value in [
                    ("Threads_connected", "3"),
                    ("Threads_running", "1"),
                    ("Uptime", "42"),
                ]
            ]
        ],
    )
    assert result.status == "ok"
    assert result.payload["uptime_seconds"] == 42
    assert cursor.execute.call_args_list[-1].args[0] == queries.HEALTH


def test_empty_lock_snapshot_and_cycle_detection():
    result, _ = run_tool("mysql.lock_chain", rows=[[]])
    assert result.status == "empty"
    assert result.payload == {"edges": [], "cycle_detected": False}
    assert has_cycle(
        [{"waiter_thread": 1, "blocker_thread": 2}, {"waiter_thread": 2, "blocker_thread": 1}]
    )
    assert not has_cycle(
        [{"waiter_thread": i, "blocker_thread": j} for i in range(12) for j in range(i + 1, 13)]
    )


def test_lock_snapshot_missing_nodes_and_truncation():
    row = {
        "waiter_thread": 1,
        "blocker_thread": 2,
        "waiter_session": None,
        "blocker_session": 2,
        "waiting_lock_type": None,
        "blocking_lock_type": "RECORD",
    }
    result, _ = run_tool("mysql.lock_chain", rows=[[row] * 101])
    assert len(result.payload["edges"]) == 100
    assert len(result.limitations) == 2


@pytest.mark.parametrize(
    "code,status",
    [(1142, "forbidden"), (1045, "forbidden"), (3024, "timeout"), (2003, "unavailable")],
)
def test_mysql_errors_are_sanitized(code, status):
    result, _ = run_tool(
        "mysql.health", error=pymysql.err.OperationalError(code, "password=secret")
    )
    assert result.status == status
    assert "secret" not in result.model_dump_json()


def test_slow_summary_units_and_missing_collection():
    consumers = [
        {"NAME": name, "ENABLED": "YES"}
        for name in ("global_instrumentation", "thread_instrumentation", "statements_digest")
    ]
    result, _ = run_tool(
        "mysql.slow_query_summary",
        rows=[
            consumers,
            [
                {
                    "digest": "abc",
                    "calls": 2,
                    "total_ps": 2e12,
                    "average_ps": 1e12,
                    "rows_examined": 9,
                }
            ],
        ],
        one={"total": 100, "disabled": 0},
    )
    assert result.payload["digests"][0]["total_seconds"] == 2
    assert result.limitations
    for rows, one in [
        ([consumers, []], {"total": 100, "disabled": 0}),
        ([consumers], {"total": 100, "disabled": 1}),
        ([[]], None),
    ]:
        result, _ = run_tool("mysql.slow_query_summary", rows=rows, one=one)
        assert result.status == "unavailable"


@pytest.mark.parametrize(
    "dsn",
    [
        DSN.replace("mysql-lab", "production"),
        DSN.replace("diag:", "root:"),
        DSN + "?local_infile=1",
    ],
)
def test_connection_allowlist(dsn):
    with pytest.raises(ValueError):
        connection_options(dsn)


def test_reject_command_inputs_before_connecting():
    adapter = MySQLDiagnostics(DSN)
    with patch("dbic.plugins.mysql.diagnostics.pymysql.connect") as connect:
        for tool, instance, params in [
            ("execute_sql", "mysql-lab-01", {}),
            ("mysql.health", "prod", {}),
            ("mysql.health", "mysql-lab-01", {"sql": "select 1"}),
        ]:
            result = asyncio.run(adapter.run(tool_id=tool, instance_id=instance, params=params))
            assert result.status == "forbidden"
        connect.assert_not_called()
