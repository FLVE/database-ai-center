from dbic.core.models import Instance

MYSQL_TOOLS = frozenset({"mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"})
REDIS_TOOLS = frozenset(
    {"redis.memory_breakdown", "redis.slowlog_summary", "redis.bigkey_audit_read"}
)
TOOLS = MYSQL_TOOLS | REDIS_TOOLS
DATABASE_TOOLS = {"mysql": MYSQL_TOOLS, "redis": REDIS_TOOLS}


def authorize_instance(instance: Instance) -> None:
    if (
        instance.type not in DATABASE_TOOLS
        or instance.id != f"{instance.type}-lab-01"
        or instance.environment != "lab"
        or not instance.enabled
        or instance.version != "8.4"
        or instance.connection_ref != f"env:{instance.type.upper()}_LAB_DSN"
    ):
        raise PermissionError("only explicitly enabled MySQL/Redis 8.4 lab instances are allowed")


def authorize(instance: Instance, tool_id: str) -> None:
    authorize_instance(instance)
    if tool_id not in DATABASE_TOOLS[instance.type]:
        raise PermissionError("tool is not in the read-only allowlist")
