"""Provision the dedicated local diagnostic account without resetting an existing account."""

import json
import re
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    compose = ["docker", "compose", "--env-file", ".env", "-f", "deploy/compose.yaml"]
    result = subprocess.run(
        compose + ["config", "--format", "json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    config = json.loads(result.stdout)
    if config["name"] != "dbic-lab":
        raise RuntimeError("only dbic-lab can be initialized")
    password = config["services"]["dbic-api"]["environment"].get("DBIC_MYSQL_DIAG_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9]{32,128}", password):
        raise RuntimeError("MYSQL_DIAG_PASSWORD must be 32-128 random alphanumeric characters")
    command = compose + [
        "exec",
        "-T",
        "mysql-lab",
        "sh",
        "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=socket '
        "-uroot --batch --skip-column-names",
    ]
    # Reusing an unrelated account could preserve unintended grants, so creation fails closed.
    sql = f"CREATE USER 'diag'@'%' IDENTIFIED BY '{password}' WITH MAX_USER_CONNECTIONS 2;\n"
    for table in (
        "global_status",
        "data_lock_waits",
        "data_locks",
        "threads",
        "setup_consumers",
        "setup_instruments",
        "events_statements_summary_by_digest",
    ):
        sql += f"GRANT SELECT ON performance_schema.{table} TO 'diag'@'%';\n"
    result = subprocess.run(
        command, cwd=root, input=sql, text=True, capture_output=True, timeout=30
    )
    if result.returncode:
        if "docker.sock" in result.stderr or "docker API" in result.stderr:
            raise RuntimeError("Docker socket is inaccessible; no lab SQL was executed")
        raise RuntimeError(
            "lab account setup failed; account may already exist. "
            "No reset attempted. Inspect its grants locally before reusing it."
        )
    print("PASS: diag created with SELECT on seven Performance Schema tables; no business grants")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f"FAIL: mysql-lab setup: {message}") from None
