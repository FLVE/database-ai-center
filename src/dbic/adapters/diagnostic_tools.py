from pydantic import JsonValue

from dbic.core.models import ToolResult, utc_now
from dbic.core.policy import MYSQL_TOOLS, REDIS_TOOLS
from dbic.core.ports import DiagnosticToolPort


class DiagnosticTools:
    def __init__(self, mysql: DiagnosticToolPort, redis: DiagnosticToolPort) -> None:
        self.mysql, self.redis = mysql, redis

    async def run(
        self, *, tool_id: str, instance_id: str, params: dict[str, JsonValue]
    ) -> ToolResult:
        if not params:
            if tool_id in MYSQL_TOOLS and instance_id == "mysql-lab-01":
                return await self.mysql.run(tool_id=tool_id, instance_id=instance_id, params={})
            if tool_id in REDIS_TOOLS and instance_id == "redis-lab-01":
                return await self.redis.run(tool_id=tool_id, instance_id=instance_id, params={})
        return ToolResult(
            source=tool_id,
            status="forbidden",
            observed_at=utc_now(),
            duration_ms=0,
            limitations=["tool, instance or parameters not allowed"],
        )
