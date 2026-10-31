import logging
from collections.abc import Callable
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, InputRequiredResult, TextContent, Tool, ToolAnnotations

from dbic.api.service import DiagnosticService
from dbic.core.models import Evidence
from dbic.core.policy import authorize

logger = logging.getLogger("dbic.mcp")
MCP_TOOLS = {
    "mysql_health": "mysql.health",
    "mysql_lock_chain": "mysql.lock_chain",
    "mysql_slow_query_summary": "mysql.slow_query_summary",
    "redis_memory_breakdown": "redis.memory_breakdown",
    "redis_slowlog_summary": "redis.slowlog_summary",
    "redis_bigkey_audit_read": "redis.bigkey_audit_read",
    "mongodb_current_ops_summary": "mongodb.current_ops_summary",
    "mongodb_replica_health": "mongodb.replica_health",
    "postgresql_blocking_sessions": "postgresql.blocking_sessions",
    "postgresql_vacuum_health": "postgresql.vacuum_health",
    "doris_fe_be_health": "doris.fe_be_health",
    "doris_query_profile_summary": "doris.query_profile_summary",
}


class DiagnosticMCP(MCPServer[None]):
    def __init__(self, service: Callable[[], DiagnosticService]) -> None:
        super().__init__("Database AI Center", version="0.9.0")
        self.service = service

    async def list_tools(self) -> list[Tool]:
        result = []
        for tool in await super().list_tools():
            tool_id = MCP_TOOLS[tool.name]
            instance = await self.service().registry.get_instance(tool_id.split(".")[0] + "-lab-01")
            if instance is None:
                continue
            try:
                authorize(instance, tool_id)
            except PermissionError:
                continue
            tool.input_schema["additionalProperties"] = False
            result.append(tool)
        return result

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[None, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        if (
            name not in MCP_TOOLS
            or set(arguments) != {"instance_id"}
            or arguments["instance_id"] != MCP_TOOLS[name].split(".")[0] + "-lab-01"
        ):
            return CallToolResult(
                isError=True, content=[TextContent(type="text", text="invalid_tool_or_arguments")]
            )
        try:
            return await super().call_tool(name, arguments, context)
        except Exception as exc:
            logger.warning("MCP tool=%s failure=%s", name, type(exc).__name__)
            return CallToolResult(
                isError=True, content=[TextContent(type="text", text="diagnostic_tool_unavailable")]
            )


def create_mcp(service: Callable[[], DiagnosticService]) -> DiagnosticMCP:
    server = DiagnosticMCP(service)
    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )

    @server.tool(annotations=annotations)
    async def mysql_health(instance_id: Literal["mysql-lab-01"]) -> Evidence:
        """Read current MySQL lab health; archive one task and its redacted evidence."""
        return await service().inspect_tool("mysql.health", instance_id)

    @server.tool(annotations=annotations)
    async def mysql_lock_chain(instance_id: Literal["mysql-lab-01"]) -> Evidence:
        """Read current InnoDB row lock waits; this does not reconstruct historical deadlocks."""
        return await service().inspect_tool("mysql.lock_chain", instance_id)

    @server.tool(annotations=annotations)
    async def mysql_slow_query_summary(instance_id: Literal["mysql-lab-01"]) -> Evidence:
        """Read cumulative statement digests; these are not time-window slow query logs."""
        return await service().inspect_tool("mysql.slow_query_summary", instance_id)

    @server.tool(annotations=annotations)
    async def redis_memory_breakdown(instance_id: Literal["redis-lab-01"]) -> Evidence:
        """Read a current Redis memory snapshot; one snapshot does not establish growth."""
        return await service().inspect_tool("redis.memory_breakdown", instance_id)

    @server.tool(annotations=annotations)
    async def redis_slowlog_summary(instance_id: Literal["redis-lab-01"]) -> Evidence:
        """Summarize retained slowlog samples without keys, arguments or client metadata."""
        return await service().inspect_tool("redis.slowlog_summary", instance_id)

    @server.tool(annotations=annotations)
    async def redis_bigkey_audit_read(instance_id: Literal["redis-lab-01"]) -> Evidence:
        """Read an existing sanitized audit; never scan Redis keys."""
        return await service().inspect_tool("redis.bigkey_audit_read", instance_id)

    @server.tool(annotations=annotations)
    async def mongodb_current_ops_summary(instance_id: Literal["mongodb-lab-01"]) -> Evidence:
        """Read bounded current-operation samples without commands or client metadata."""
        return await service().inspect_tool("mongodb.current_ops_summary", instance_id)

    @server.tool(annotations=annotations)
    async def mongodb_replica_health(instance_id: Literal["mongodb-lab-01"]) -> Evidence:
        """Read single-member lab replica state; this does not establish high availability."""
        return await service().inspect_tool("mongodb.replica_health", instance_id)

    @server.tool(annotations=annotations)
    async def postgresql_blocking_sessions(instance_id: Literal["postgresql-lab-01"]) -> Evidence:
        """Read bounded current lock waits; never cancel or terminate sessions."""
        return await service().inspect_tool("postgresql.blocking_sessions", instance_id)

    @server.tool(annotations=annotations)
    async def postgresql_vacuum_health(instance_id: Literal["postgresql-lab-01"]) -> Evidence:
        """Read tuple estimates and maintenance timestamps; never execute VACUUM."""
        return await service().inspect_tool("postgresql.vacuum_health", instance_id)

    @server.tool(annotations=annotations)
    async def doris_fe_be_health(instance_id: Literal["doris-lab-01"]) -> Evidence:
        """Read current FE reachability and BE counts in the isolated Doris lab."""
        return await service().inspect_tool("doris.fe_be_health", instance_id)

    @server.tool(annotations=annotations)
    async def doris_query_profile_summary(instance_id: Literal["doris-lab-01"]) -> Evidence:
        """Read bounded summaries of diag-owned profiles; no SQL or full profile text."""
        return await service().inspect_tool("doris.query_profile_summary", instance_id)

    return server
