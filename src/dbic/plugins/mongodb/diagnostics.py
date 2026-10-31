import asyncio
import logging
import re
import time
from typing import Any

from pydantic import JsonValue, SecretStr
from pymongo import AsyncMongoClient
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

from dbic.core.models import EvidenceStatus, ToolResult, utc_now
from dbic.core.policy import MONGODB_TOOLS
from dbic.plugins.mongodb.models import current_operations, replica_snapshot

APP_NAME = "dbic-diagnostics"
CURRENT_PIPELINE: list[dict[str, Any]] = [
    {
        "$currentOp": {
            "allUsers": True,
            "idleConnections": False,
            "idleCursors": False,
            "idleSessions": False,
        }
    },
    {"$match": {"active": True, "appName": {"$ne": APP_NAME}}},
    {"$project": {"_id": 0, "op": 1, "secs_running": 1, "waitingForLock": 1}},
    {"$limit": 51},
]


class MongoDBDiagnostics:
    def __init__(self, password: SecretStr | None) -> None:
        self.password = password

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

        if tool_id not in MONGODB_TOOLS or instance_id != "mongodb-lab-01" or params:
            return result("forbidden", "tool, instance or parameters not allowed")
        if not self.password or not self.password.get_secret_value():
            return result("unavailable", "MongoDB diagnostic credentials not configured")
        try:
            async with asyncio.timeout(5):
                async with AsyncMongoClient[dict[str, Any]](
                    host="mongodb-lab",
                    port=27017,
                    username="diag",
                    password=self.password.get_secret_value(),
                    authSource="admin",
                    directConnection=True,
                    replicaSet="dbic-rs",
                    appname=APP_NAME,
                    retryReads=False,
                    retryWrites=False,
                    maxPoolSize=1,
                    serverSelectionTimeoutMS=1000,
                    connectTimeoutMS=1000,
                    socketTimeoutMS=2000,
                    timeoutMS=4000,
                ) as client:
                    version = (await client.admin.command("buildInfo")).get("version", "")
                    hello = await client.admin.command("hello")
                    if (
                        not isinstance(version, str)
                        or re.fullmatch(r"8\.0\.\d+", version) is None
                        or hello.get("setName") != "dbic-rs"
                        or hello.get("msg") == "isdbgrid"
                        or hello.get("hosts") != ["mongodb-lab:27017"]
                        or hello.get("arbiters")
                        or hello.get("passives")
                    ):
                        return result("unavailable", "only MongoDB 8.0 dbic-rs lab is supported")
                    if tool_id == "mongodb.current_ops_summary":
                        cursor = await client.admin.aggregate(
                            CURRENT_PIPELINE, maxTimeMS=1500, allowDiskUse=False, batchSize=51
                        )
                        async with cursor:
                            summary = current_operations(await cursor.to_list(length=51))
                        payload = summary.model_dump(mode="json")
                        status: EvidenceStatus = "ok" if summary.samples else "empty"
                        limits = ["bounded active-operation snapshot; not historical slow queries"]
                        if summary.truncated:
                            limits.append("active-operation sample truncated at 50")
                        if any(
                            s.running_seconds is None or s.waiting_for_lock is None
                            for s in summary.samples
                        ):
                            limits.append("operation duration or lock-wait field missing")
                    else:
                        replica = replica_snapshot(
                            await client.admin.command({"replSetGetStatus": 1, "maxTimeMS": 1500})
                        )
                        payload, status = replica.model_dump(mode="json"), "ok"
                        limits = ["single-member lab; no redundancy or replication-lag validation"]
                    return ToolResult(
                        source=tool_id,
                        status=status,
                        observed_at=observed,
                        duration_ms=(time.monotonic() - started) * 1000,
                        payload=payload,
                        limitations=limits,
                    )
        except OperationFailure as exc:
            if exc.code in {13, 18}:
                return result("forbidden", "MongoDB diagnostic permission denied")
            if exc.timeout:
                return result("timeout", "MongoDB diagnostic timed out")
            if exc.code in {76, 94}:
                return result("unavailable", "MongoDB replica set unavailable")
            logging.getLogger("dbic.mongodb").warning("tool=%s failure=OperationFailure", tool_id)
            return result("error", "MongoDB diagnostic command failed")
        except TimeoutError:
            return result("timeout", "MongoDB diagnostic timed out")
        except ConnectionFailure as exc:
            return result("timeout" if exc.timeout else "unavailable", "MongoDB connection failed")
        except LookupError:
            return result("unavailable", "MongoDB replica topology unsupported")
        except (PyMongoError, ValueError, TypeError, AttributeError) as exc:
            logging.getLogger("dbic.mongodb").warning(
                "tool=%s failure=%s", tool_id, type(exc).__name__
            )
            return result("error", "invalid MongoDB diagnostic response")
