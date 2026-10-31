from pydantic import JsonValue

from dbic.core.models import ToolResult, utc_now
from dbic.core.policy import DORIS_TOOLS, MONGODB_TOOLS, MYSQL_TOOLS, POSTGRESQL_TOOLS, REDIS_TOOLS
from dbic.core.ports import DiagnosticToolPort


class DiagnosticTools:
    def __init__(
        self,
        mysql: DiagnosticToolPort,
        redis: DiagnosticToolPort,
        mongodb: DiagnosticToolPort | None = None,
        postgresql: DiagnosticToolPort | None = None,
        doris: DiagnosticToolPort | None = None,
    ) -> None:
        self.mysql, self.redis = mysql, redis
        self.mongodb = mongodb
        self.postgresql = postgresql
        self.doris = doris

    async def run(
        self, *, tool_id: str, instance_id: str, params: dict[str, JsonValue]
    ) -> ToolResult:
        if not params:
            if tool_id in DORIS_TOOLS and instance_id == "doris-lab-01" and self.doris:
                return await self.doris.run(tool_id=tool_id, instance_id=instance_id, params={})
            if (
                tool_id in POSTGRESQL_TOOLS
                and instance_id == "postgresql-lab-01"
                and self.postgresql
            ):
                return await self.postgresql.run(
                    tool_id=tool_id, instance_id=instance_id, params={}
                )
            if tool_id in MYSQL_TOOLS and instance_id == "mysql-lab-01":
                return await self.mysql.run(tool_id=tool_id, instance_id=instance_id, params={})
            if tool_id in REDIS_TOOLS and instance_id == "redis-lab-01":
                return await self.redis.run(tool_id=tool_id, instance_id=instance_id, params={})
            if tool_id in MONGODB_TOOLS and instance_id == "mongodb-lab-01" and self.mongodb:
                return await self.mongodb.run(tool_id=tool_id, instance_id=instance_id, params={})
        return ToolResult(
            source=tool_id,
            status="forbidden",
            observed_at=utc_now(),
            duration_ms=0,
            limitations=["tool, instance or parameters not allowed"],
        )
