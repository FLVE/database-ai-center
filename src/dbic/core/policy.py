from dbic.core.models import Instance

TOOLS = frozenset({"mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"})


def authorize(instance: Instance, tool_id: str) -> None:
    if (
        instance.id != "mysql-lab-01"
        or instance.type != "mysql"
        or instance.environment != "lab"
        or not instance.enabled
        or instance.version != "8.4"
        or instance.connection_ref != "env:MYSQL_LAB_DSN"
    ):
        raise PermissionError("only enabled mysql-lab-01 / MySQL 8.4 is allowed")
    if tool_id not in TOOLS:
        raise PermissionError("tool is not in the read-only allowlist")
