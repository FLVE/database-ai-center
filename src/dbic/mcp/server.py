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
}


class DiagnosticMCP(MCPServer[None]):
    def __init__(self, service: Callable[[], DiagnosticService]) -> None:
        super().__init__("Database AI Center", version="0.4.0")
        self.service = service

    async def list_tools(self) -> list[Tool]:
        instance = await self.service().registry.get_instance("mysql-lab-01")
        if instance is None:
            return []
        try:
            authorize(instance, "mysql.health")
        except PermissionError:
            return []
        result = await super().list_tools()
        for tool in result:
            tool.input_schema["additionalProperties"] = False
        return result

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[None, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        if (
            name not in MCP_TOOLS
            or set(arguments) != {"instance_id"}
            or arguments["instance_id"] != "mysql-lab-01"
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

    return server
