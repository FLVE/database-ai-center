import asyncio
import json
import logging
import re
import time
from typing import Any, cast

import httpx
import pymysql
from pydantic import JsonValue, SecretStr

from dbic.adapters.http import BackendFailure
from dbic.core.doris_models import BackendHealth, ProfileSummary
from dbic.core.models import EvidenceStatus, ToolResult, utc_now
from dbic.core.policy import DORIS_TOOLS


def duration_ms(value: object) -> float:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("invalid duration")
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|us|ns|sec|h|m|s)", value)
    if not parts or "".join(n + u for n, u in parts) != value:
        raise ValueError("invalid duration")
    units = {"h": 3600000, "m": 60000, "s": 1000, "sec": 1000, "ms": 1, "us": 0.001, "ns": 0.000001}
    return sum(float(n) * units[u] for n, u in parts)


def profile_summary(data: dict[str, Any]) -> ProfileSummary:
    columns = data["column_names"]
    if columns != [
        "Profile ID",
        "Task Type",
        "Start Time",
        "End Time",
        "Total",
        "Task State",
        "User",
        "Default Catalog",
        "Default Db",
        "Sql Statement",
    ]:
        raise ValueError("unsupported Profile columns")
    rows = data["rows"]
    if not isinstance(rows, list):
        raise ValueError("invalid Profile rows")
    owned = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("User"), str):
            raise ValueError("invalid Profile row")
        if row["User"] == "diag":
            owned.append(row)
    samples = []
    for row in owned[:20]:
        state = row["Task State"]
        if not isinstance(state, str):
            raise ValueError("invalid Profile state")
        samples.append(
            {
                "duration_ms": duration_ms(row["Total"]),
                "state": state
                if state in {"OK", "EOF", "FINISHED", "CANCELLED", "ERROR", "RUNNING"}
                else "OTHER",
            }
        )
    return ProfileSummary.model_validate({"samples": samples, "truncated": len(owned) > 20})


class DorisDiagnostics:
    def __init__(
        self, password: SecretStr | None, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.password, self.transport = password, transport

    def _version(self) -> None:
        assert self.password
        with (
            pymysql.connect(
                host="doris-fe",
                port=9030,
                user="diag",
                password=self.password.get_secret_value(),
                connect_timeout=1,
                read_timeout=1,
                write_timeout=1,
                autocommit=True,
            ) as conn,
            conn.cursor() as cursor,
        ):
            cursor.execute("SELECT @@version_comment")
            row = cursor.fetchone()
            if (
                not row
                or not isinstance(row[0], str)
                or not row[0].startswith("Doris version doris-3.0.8-")
            ):
                raise BackendFailure("unavailable", "unsupported Doris version; require 3.0.8")

    async def _get(self, path: str) -> dict[str, Any]:
        assert self.password
        async with httpx.AsyncClient(
            base_url="http://doris-fe:8030",
            auth=("diag", self.password.get_secret_value()),
            trust_env=False,
            follow_redirects=False,
            timeout=3,
            transport=self.transport,
        ) as client:
            async with client.stream("GET", path) as response:
                if response.status_code in {401, 403}:
                    raise BackendFailure("forbidden", "Doris authentication or permission denied")
                if response.status_code != 200:
                    raise BackendFailure("unavailable", "Doris HTTP endpoint unavailable")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise BackendFailure("error", "Doris response exceeds 256 KiB limit")
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError("invalid response envelope")
                if body.get("code") in {401, 403}:
                    raise BackendFailure("forbidden", "Doris authentication or permission denied")
                if type(body.get("code")) is not int or body["code"] != 0:
                    raise BackendFailure("unavailable", "Doris endpoint reported failure")
                if not isinstance(body.get("data"), dict):
                    raise ValueError("invalid response data")
                return cast(dict[str, Any], body["data"])

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

        if tool_id not in DORIS_TOOLS or instance_id != "doris-lab-01" or params:
            return failure("forbidden", "tool, instance or parameters not allowed")
        if not self.password or not self.password.get_secret_value():
            return failure("unavailable", "Doris diagnostic credentials not configured")
        try:
            async with asyncio.timeout(5):
                await asyncio.to_thread(self._version)
                if tool_id == "doris.fe_be_health":
                    data = await self._get("/api/health")
                    health = BackendHealth.model_validate(
                        {
                            "online_backends": data["online_backend_num"],
                            "total_backends": data["total_backend_num"],
                        }
                    )
                    payload, empty = health.model_dump(mode="json"), False
                    limits = [
                        "current FE reachability and BE counts only; no HA or tablet assessment"
                    ]
                else:
                    # 3.0.8's v2 endpoint fails on populated profiles; v1 is read-only.
                    data = await self._get("/rest/v1/query_profile/")
                    profiles = profile_summary(data)
                    payload, empty = profiles.model_dump(mode="json"), not profiles.samples
                    limits = [
                        "only retained profiles owned by diag on this FE; "
                        "not business query coverage"
                    ]
                    if profiles.truncated:
                        limits.append("profile samples truncated at 20")
                return ToolResult(
                    source=tool_id,
                    status="empty" if empty else "ok",
                    observed_at=observed,
                    duration_ms=(time.monotonic() - started) * 1000,
                    payload=payload,
                    limitations=limits,
                )
        except BackendFailure as exc:
            return failure(exc.status, exc.code)
        except (TimeoutError, httpx.TimeoutException):
            return failure("timeout", "Doris diagnostic timed out")
        except pymysql.MySQLError as exc:
            if exc.args and exc.args[0] in {1045, 1142, 1227}:
                return failure("forbidden", "Doris diagnostic permission denied")
            return failure("unavailable", "Doris version probe unavailable")
        except httpx.RequestError:
            return failure("unavailable", "Doris HTTP connection unavailable")
        except (ValueError, TypeError, KeyError) as exc:
            logging.getLogger("dbic.doris").warning(
                "tool=%s failure=%s", tool_id, type(exc).__name__
            )
            return failure("error", "invalid Doris diagnostic response")
