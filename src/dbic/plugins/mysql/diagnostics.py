import asyncio
import json
import logging
import threading
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import pymysql
from pydantic import JsonValue

from dbic.core.models import EvidenceStatus, ToolResult, utc_now
from dbic.core.policy import TOOLS
from dbic.plugins.mysql import queries

logger = logging.getLogger("dbic.mysql")


def connection_options(dsn: str) -> dict[str, Any]:
    url = urlsplit(dsn)
    if (
        url.scheme != "mysql+pymysql"
        or url.hostname != "mysql-lab"
        or url.port not in (None, 3306)
        or unquote(url.username or "") != "diag"
        or url.path != "/performance_schema"
        or url.query
        or url.fragment
        or not url.password
    ):
        raise ValueError("MySQL DSN must target diag@mysql-lab:3306/performance_schema only")
    return dict(
        host="mysql-lab",
        port=3306,
        user="diag",
        password=unquote(url.password),
        database="performance_schema",
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=3,
        read_timeout=5,
        write_timeout=5,
        local_infile=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def has_cycle(edges: list[dict[str, Any]]) -> bool:
    graph: dict[int, set[int]] = {}
    for edge in edges:
        if edge["waiter_thread"] is not None and edge["blocker_thread"] is not None:
            graph.setdefault(int(edge["waiter_thread"]), set()).add(int(edge["blocker_thread"]))

    active: set[int] = set()
    finished: set[int] = set()

    def visit(node: int) -> bool:
        if node in active:
            return True
        if node in finished:
            return False
        active.add(node)
        if any(visit(child) for child in graph.get(node, set())):
            return True
        active.remove(node)
        finished.add(node)
        return False

    return any(visit(node) for node in graph)


class MySQLDiagnostics:
    def __init__(self, dsn: str | None) -> None:
        self.options = connection_options(dsn) if dsn else None
        self._gate = threading.BoundedSemaphore(1)

    async def run(
        self, *, tool_id: str, instance_id: str, params: dict[str, JsonValue]
    ) -> ToolResult:
        if tool_id not in TOOLS or instance_id != "mysql-lab-01" or params:
            return ToolResult(
                status="forbidden",
                source=tool_id,
                observed_at=utc_now(),
                duration_ms=0,
                limitations=["tool, instance or parameters not allowed"],
            )
        return await asyncio.to_thread(self._run, tool_id)

    def _run(self, tool_id: str) -> ToolResult:
        started = time.monotonic()

        def result(
            status: EvidenceStatus,
            payload: dict[str, JsonValue] | None = None,
            limitations: list[str] | None = None,
        ) -> ToolResult:
            return ToolResult(
                status=status,
                source=tool_id,
                observed_at=utc_now(),
                duration_ms=(time.monotonic() - started) * 1000,
                payload=payload or {},
                limitations=limitations or [],
            )

        if self.options is None:
            return result(
                "unavailable", limitations=["mysql lab diagnostic credentials not configured"]
            )
        if not self._gate.acquire(timeout=1):
            return result("unavailable", limitations=["instance concurrency budget exhausted"])
        try:
            with pymysql.connect(**self.options) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute(queries.VERSION)
                    version = cursor.fetchone()
                    if not version or not str(version["version"]).startswith("8.4."):
                        return result("unavailable", limitations=["only MySQL 8.4 is supported"])
                    if not version["enabled"]:
                        return result("unavailable", limitations=["performance_schema disabled"])
                    limitations: list[str] = []
                    payload: dict[str, JsonValue]
                    if tool_id == "mysql.health":
                        cursor.execute(queries.HEALTH)
                        rows = cursor.fetchmany(4)
                        values = {
                            r["VARIABLE_NAME"].lower(): int(r["VARIABLE_VALUE"]) for r in rows
                        }
                        if set(values) != {"threads_connected", "threads_running", "uptime"}:
                            return result(
                                "unavailable", limitations=["required health counters missing"]
                            )
                        payload = {
                            "version": str(version["version"]),
                            "threads_connected": values["threads_connected"],
                            "threads_running": values["threads_running"],
                            "uptime_seconds": values["uptime"],
                        }
                    elif tool_id == "mysql.lock_chain":
                        cursor.execute(queries.LOCKS)
                        edges = list(cursor.fetchmany(101))
                        if len(edges) > 100:
                            limitations.append("lock edges truncated at 100")
                        edges = edges[:100]
                        if any(
                            e["waiter_session"] is None
                            or e["blocker_session"] is None
                            or e["waiting_lock_type"] is None
                            or e["blocking_lock_type"] is None
                            for e in edges
                        ):
                            limitations.append("sampling changed or lock/session nodes are missing")
                        payload = {"edges": edges, "cycle_detected": has_cycle(edges)}
                    else:
                        cursor.execute(queries.CONSUMERS)
                        consumers = cursor.fetchmany(4)
                        if len(consumers) != 3 or any(c["ENABLED"] != "YES" for c in consumers):
                            return result(
                                "unavailable", limitations=["digest consumers disabled or missing"]
                            )
                        cursor.execute(queries.INSTRUMENTS)
                        instruments = cursor.fetchone()
                        if not instruments or not instruments["total"] or instruments["disabled"]:
                            return result(
                                "unavailable", limitations=["statement instrumentation incomplete"]
                            )
                        cursor.execute(queries.DIGESTS)
                        rows = cursor.fetchmany(21)
                        if not rows:
                            return result(
                                "unavailable", limitations=["no timed application digest samples"]
                            )
                        limitations.append(
                            "cumulative since reset; not slow-log history or a time-window delta"
                        )
                        if len(rows) > 20:
                            limitations.append("top 20 by cumulative time; other digests omitted")
                        digests: list[JsonValue] = [
                            {
                                "digest": r["digest"],
                                "calls": int(r["calls"]),
                                "total_seconds": float(r["total_ps"]) / 1e12,
                                "average_seconds": float(r["average_ps"]) / 1e12,
                                "rows_examined": int(r["rows_examined"]),
                            }
                            for r in rows[:20]
                        ]
                        payload = {"digests": digests}
                    if len(json.dumps(payload).encode()) > 65536:
                        return result("error", limitations=["result_size_limit"])
                    status: EvidenceStatus = "empty" if payload.get("edges") == [] else "ok"
                    return result(status, payload, limitations)
        except pymysql.MySQLError as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else 0
            logger.warning("tool=%s instance=mysql-lab-01 mysql_error_code=%s", tool_id, code)
            if code in {1044, 1045, 1142, 1143, 1227}:
                return result(
                    "forbidden", limitations=["mysql diagnostic account permission denied"]
                )
            if code in {3024, 2013, 1205}:
                return result(
                    "timeout", limitations=["mysql diagnostic query timed out or connection lost"]
                )
            return result("unavailable", limitations=["mysql diagnostic source unavailable"])
        finally:
            self._gate.release()
