import asyncio
import logging
import time

import psycopg
from psycopg.rows import dict_row
from pydantic import JsonValue, SecretStr

from dbic.core.models import EvidenceStatus, ToolResult, utc_now
from dbic.core.policy import POSTGRESQL_TOOLS
from dbic.core.postgresql_models import BlockingSnapshot, VacuumSnapshot
from dbic.plugins.postgresql import queries


class PostgreSQLDiagnostics:
    def __init__(self, password: SecretStr | None) -> None:
        self.password = password

    async def run(
        self, *, tool_id: str, instance_id: str, params: dict[str, JsonValue]
    ) -> ToolResult:
        started, observed = time.monotonic(), utc_now()

        def failure(status: EvidenceStatus, reason: str) -> ToolResult:
            return ToolResult(
                source=tool_id,
                status=status,
                observed_at=observed,
                duration_ms=(time.monotonic() - started) * 1000,
                limitations=[reason],
            )

        if tool_id not in POSTGRESQL_TOOLS or instance_id != "postgresql-lab-01" or params:
            return failure("forbidden", "tool, instance or parameters not allowed")
        if not self.password or not self.password.get_secret_value():
            return failure("unavailable", "PostgreSQL diagnostic credentials not configured")
        try:
            async with asyncio.timeout(5):
                async with await psycopg.AsyncConnection.connect(
                    host="postgresql-lab",
                    port=5432,
                    dbname="dbic_lab",
                    user="diag",
                    password=self.password.get_secret_value(),
                    connect_timeout=2,
                    row_factory=dict_row,
                    application_name="dbic-diagnostics",
                    options="-c default_transaction_read_only=on -c statement_timeout=1500 "
                    "-c lock_timeout=500 -c search_path=pg_catalog",
                ) as conn:
                    check = await (await conn.execute(queries.CHECK)).fetchone()
                    if (
                        not check
                        or not 170000 <= check["version"] < 180000
                        or check["database"] != "dbic_lab"
                    ):
                        return failure(
                            "unavailable", "only PostgreSQL 17 diagnostic lab is supported"
                        )
                    if check["allowed"] is not True:
                        return failure(
                            "forbidden", "pg_read_all_stats required for complete visibility"
                        )
                    if tool_id == "postgresql.blocking_sessions":
                        rows = await (await conn.execute(queries.BLOCKING)).fetchall()
                        valid = [r for r in rows[:50] if r["blocker_pids"]]
                        snapshot = BlockingSnapshot.model_validate(
                            {"sessions": valid, "truncated": len(rows) > 50}
                        )
                        payload = snapshot.model_dump(mode="json")
                        empty = not snapshot.sessions
                        limits = [
                            "current lock snapshot only; sampling can change during collection"
                        ]
                        if len(valid) != len(rows[:50]):
                            limits.append("wait ended during sampling")
                        if any(0 in r.blocker_pids for r in snapshot.sessions):
                            limits.append("prepared transaction blocker has no session PID")
                    else:
                        if check["tracked"] is not True:
                            return failure("unavailable", "PostgreSQL tuple statistics disabled")
                        rows = await (await conn.execute(queries.VACUUM)).fetchall()
                        reset = await (await conn.execute(queries.RESET)).fetchone()
                        if reset is None:
                            return failure("error", "database statistics row missing")
                        vacuum = VacuumSnapshot.model_validate(
                            {
                                "tables": rows[:50],
                                "truncated": len(rows) > 50,
                                "stats_reset_at": reset["stats_reset"],
                            }
                        )
                        payload, empty = vacuum.model_dump(mode="json"), not vacuum.tables
                        limits = [
                            "tuple counts are estimates since statistics reset; not physical bloat"
                        ]
                    if len(rows) > 50:
                        limits.append("sample truncated at 50")
                    return ToolResult(
                        source=tool_id,
                        status="empty" if empty else "ok",
                        observed_at=observed,
                        duration_ms=(time.monotonic() - started) * 1000,
                        payload=payload,
                        limitations=limits,
                    )
        except TimeoutError:
            return failure("timeout", "PostgreSQL diagnostic timed out")
        except psycopg.Error as exc:
            code = exc.sqlstate
            if code in {"42501", "28000", "28P01"}:
                return failure("forbidden", "PostgreSQL diagnostic permission denied")
            if code in {"57014", "55P03"}:
                return failure("timeout", "PostgreSQL statement budget exceeded")
            logging.getLogger("dbic.postgresql").warning(
                "tool=%s failure=%s", tool_id, type(exc).__name__
            )
            return failure("unavailable", "PostgreSQL diagnostic source unavailable")
        except (ValueError, TypeError, KeyError) as exc:
            logging.getLogger("dbic.postgresql").warning(
                "tool=%s failure=%s", tool_id, type(exc).__name__
            )
            return failure("error", "invalid PostgreSQL diagnostic response")
