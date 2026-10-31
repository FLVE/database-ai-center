import asyncio
import logging
import re
import time
from pathlib import Path

from pydantic import JsonValue, SecretStr
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import AuthenticationError, ConnectionError, NoPermissionError, ResponseError
from redis.exceptions import TimeoutError as RedisTimeout

from dbic.core.models import EvidenceStatus, ToolResult, utc_now
from dbic.core.policy import REDIS_TOOLS
from dbic.plugins.redis.audit import read_audit
from dbic.plugins.redis.models import memory_snapshot, slowlog_summary

logger = logging.getLogger("dbic.redis")
SLOWLOG_LIMIT = "retained slowlog samples only; empty does not prove no slow commands"
AUDIT_LIMIT = "imported audit scope and time only; not a live scan or confirmed cause"


class RedisDiagnostics:
    def __init__(self, password: SecretStr | None, audit_dir: Path | None = None) -> None:
        self.password, self.audit_dir = password, audit_dir

    async def run(
        self, *, tool_id: str, instance_id: str, params: dict[str, JsonValue]
    ) -> ToolResult:
        started, observed = time.monotonic(), utc_now()

        def result(status: EvidenceStatus, limitation: str) -> ToolResult:
            return ToolResult(
                source=tool_id,
                status=status,
                observed_at=observed,
                duration_ms=(time.monotonic() - started) * 1000,
                limitations=[limitation],
            )

        if tool_id not in REDIS_TOOLS or instance_id != "redis-lab-01" or params:
            return result("forbidden", "tool, instance or parameters not allowed")
        try:
            async with asyncio.timeout(5):
                if tool_id == "redis.bigkey_audit_read":
                    if self.audit_dir is None:
                        return result("unavailable", "Redis audit file not configured")
                    audit = await asyncio.to_thread(read_audit, self.audit_dir, observed)
                    return ToolResult(
                        source=tool_id,
                        status="ok",
                        observed_at=audit.finished_at,
                        duration_ms=(time.monotonic() - started) * 1000,
                        payload=audit.model_dump(mode="json") | {"origin": "imported_audit"},
                        limitations=[AUDIT_LIMIT],
                    )
                if not self.password or not self.password.get_secret_value():
                    return result("unavailable", "Redis diagnostic credentials not configured")
                async with Redis(
                    host="redis-lab",
                    port=6379,
                    db=0,
                    username="diag",
                    password=self.password.get_secret_value(),
                    protocol=2,
                    socket_connect_timeout=1,
                    socket_timeout=2,
                    max_connections=1,
                    retry=Retry(NoBackoff(), 0),
                    health_check_interval=0,
                    driver_info=None,
                ) as client:
                    server = await client.info("server")
                    version = server.get("redis_version", "")
                    if (
                        not isinstance(version, str)
                        or re.fullmatch(r"8\.4\.\d+", version) is None
                        or server.get("redis_mode") != "standalone"
                    ):
                        return result("unavailable", "only standalone Redis 8.4 is supported")
                    if tool_id == "redis.memory_breakdown":
                        memory = memory_snapshot(
                            await client.info("memory"), await client.info("stats")
                        )
                        missing = [
                            name for name, value in memory.model_dump().items() if value is None
                        ]
                        return ToolResult(
                            source=tool_id,
                            status="ok",
                            observed_at=observed,
                            duration_ms=(time.monotonic() - started) * 1000,
                            payload=memory.model_dump(mode="json", exclude_none=True),
                            limitations=[
                                "missing or inconsistent Redis memory fields: " + ", ".join(missing)
                            ]
                            if missing
                            else [],
                        )
                    # Avoid flattening argv so argument bytes never become command names.
                    client.set_response_callback("SLOWLOG GET", lambda response, **kwargs: response)
                    summary = slowlog_summary(await client.slowlog_get(20), observed)
                    return ToolResult(
                        source=tool_id,
                        status="ok" if summary.sample_count else "empty",
                        observed_at=observed,
                        duration_ms=(time.monotonic() - started) * 1000,
                        payload=summary.model_dump(mode="json"),
                        limitations=[SLOWLOG_LIMIT],
                    )
        except (AuthenticationError, NoPermissionError, PermissionError):
            return result("forbidden", "Redis diagnostic source permission denied")
        except (RedisTimeout, TimeoutError):
            return result("timeout", "Redis diagnostic source timed out")
        except (ConnectionError, FileNotFoundError, LookupError):
            return result("unavailable", "Redis diagnostic source unavailable or audit stale")
        except (ValueError, OSError, ResponseError, TypeError, OverflowError) as exc:
            logger.warning("Redis tool=%s failure=%s", tool_id, type(exc).__name__)
            return result("error", "invalid Redis diagnostic response or audit")
