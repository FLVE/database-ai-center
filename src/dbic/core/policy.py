from dbic.core.models import Instance

MYSQL_TOOLS = frozenset({"mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"})
REDIS_TOOLS = frozenset(
    {"redis.memory_breakdown", "redis.slowlog_summary", "redis.bigkey_audit_read"}
)
MONGODB_TOOLS = frozenset({"mongodb.current_ops_summary", "mongodb.replica_health"})
POSTGRESQL_TOOLS = frozenset({"postgresql.blocking_sessions", "postgresql.vacuum_health"})
DORIS_TOOLS = frozenset({"doris.fe_be_health", "doris.query_profile_summary"})
TOOLS = MYSQL_TOOLS | REDIS_TOOLS | MONGODB_TOOLS | POSTGRESQL_TOOLS | DORIS_TOOLS
DATABASE_TOOLS = {
    "mysql": MYSQL_TOOLS,
    "redis": REDIS_TOOLS,
    "mongodb": MONGODB_TOOLS,
    "postgresql": POSTGRESQL_TOOLS,
    "doris": DORIS_TOOLS,
}
DATABASE_VERSIONS = {
    "mysql": "8.4",
    "redis": "8.4",
    "mongodb": "8.0",
    "postgresql": "17",
    "doris": "3.0.8",
}


def authorize_instance(instance: Instance) -> None:
    if (
        instance.type not in DATABASE_TOOLS
        or instance.id != f"{instance.type}-lab-01"
        or instance.environment != "lab"
        or not instance.enabled
        or instance.version != DATABASE_VERSIONS[instance.type]
        or instance.connection_ref != f"env:{instance.type.upper()}_LAB_DSN"
    ):
        raise PermissionError("only explicitly enabled supported lab instances are allowed")


def authorize(instance: Instance, tool_id: str) -> None:
    authorize_instance(instance)
    if tool_id not in DATABASE_TOOLS[instance.type]:
        raise PermissionError("tool is not in the read-only allowlist")
