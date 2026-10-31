"""Run against dbic-lab only; keep credentials in memory and fixture writes rolled back."""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "deploy/compose.yaml"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, *, input_text=None):
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=120,
    )
    require(result.returncode == 0, f"subprocess exited {result.returncode}")
    return result.stdout


def http_check(path, expected_status, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request("http://127.0.0.1:8086" + path, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        require(response.status == expected_status, f"{path}: HTTP {response.status}")
        body = json.load(response)
        request_id = response.headers.get("X-Request-ID")
        require(bool(request_id), f"{path}: missing request ID")
        if expected_status == 401:
            require(body.get("request_id") == request_id, "error request ID mismatch")
    return body


def database_checks():
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import IntegrityError

    from dbic.adapters.persistence.postgres import database_ready
    from dbic.settings import Settings

    config = Settings()
    url = config.postgres_url()
    require(
        config.env == "lab" and url.host == "dbic-postgres" and url.database == "dbic",
        "database verification requires the Compose dbic lab",
    )
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        require(database_ready(engine), "lab schema is not ready")
        for _ in range(2):
            run([sys.executable, "-m", "alembic", "upgrade", "head"])
        require(database_ready(engine), "lab schema failed after repeated upgrade")
        print("PASS: dbic upgrade head repeated twice", flush=True)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = 'dbic_test'")
            ).scalar()
            if not exists:
                connection.execute(text("CREATE DATABASE dbic_test"))
    finally:
        engine.dispose()

    test_url = url.set(database="dbic_test")
    os.environ["DBIC_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    for _ in range(2):
        run([sys.executable, "-m", "alembic", "upgrade", "head"])
    engine = create_engine(test_url, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        require(database_ready(engine), "test schema is not ready")
        inspector = inspect(engine)
        expected = {
            "instances",
            "diagnostic_tasks",
            "diagnostic_steps",
            "evidence",
            "findings",
            "reports",
            "feedback",
            "audit_events",
        }
        require(expected <= set(inspector.get_table_names()), "missing system tables")
        require(inspector.get_foreign_keys("diagnostic_tasks"), "missing task foreign keys")
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                try:
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO instances "
                                "(id,type,environment,version,connection_ref) "
                                "VALUES ('m0-rejected-production','mysql','production','8.4',"
                                "'env:MYSQL_LAB_DSN')"
                            )
                        )
                except IntegrityError as exc:
                    require(
                        getattr(exc.orig, "sqlstate", None) == "23514",
                        "expected a CHECK constraint violation",
                    )
                    require(
                        exc.orig.diag.constraint_name == "ck_instances_lab",
                        "wrong constraint rejected the fixture",
                    )
                else:
                    raise RuntimeError("production instance was accepted")
            finally:
                transaction.rollback()
        print(
            "PASS: dbic_test migrations, eight tables, foreign keys and lab constraint", flush=True
        )
    finally:
        engine.dispose()


def host_checks(*, lock=False):
    os.chdir(ROOT)
    print("Checking Compose access (requires local Docker socket access)", flush=True)
    run(COMPOSE + ["ps", "-a"])
    require(http_check("/healthz", 200) == {"status": "ok"}, "health response mismatch")
    require(http_check("/readyz", 200) == {"status": "ready"}, "readiness response mismatch")
    print("PASS: host healthz and readyz", flush=True)
    http_check("/api/v1/capabilities", 401)
    http_check("/api/v1/capabilities", 401, "deliberately-invalid-token")
    token = run(
        COMPOSE
        + [
            "exec",
            "-T",
            "dbic-api",
            "python",
            "-c",
            "import os; print(os.environ['DBIC_API_TOKEN'])",
        ]
    ).strip()
    require(bool(token), "container token is empty")
    body = http_check("/api/v1/capabilities", 200, token)
    databases = body.get("databases", [])
    require(body.get("milestone") == "M0" and len(databases) == 5, "invalid capabilities")
    require(
        {item["database_type"] for item in databases}
        == {"mysql", "redis", "mongodb", "postgresql", "doris"},
        "database types mismatch",
    )
    require(
        all(item["status"] == "not_implemented" and item["tools"] == [] for item in databases),
        "M0 must not advertise executable tools",
    )
    http_check("/openapi.json", 401)
    require(
        set(http_check("/openapi.json", 200, token)["paths"])
        == {"/healthz", "/readyz", "/api/v1/capabilities"},
        "unexpected M0 endpoints",
    )
    print("PASS: missing/wrong/valid token, capabilities and protected OpenAPI", flush=True)
    print("Checking real PostgreSQL migrations and dedicated dbic_test", flush=True)
    output = run(
        COMPOSE + ["exec", "-T", "dbic-api", "python", "-", "--database"],
        input_text=Path(__file__).read_text(),
    )
    print(output, end="")
    require(http_check("/readyz", 200) == {"status": "ready"}, "post-test readiness failed")
    print("PASS: M0 live verification complete; dbic_test retained, fixture writes rolled back")
    if lock:
        print("Generating dependency lock and checking the locked environment", flush=True)
        run(["uv", "lock"])
        run(["uv", "sync", "--locked", "--python", "3.12"])
        print(run(["uv", "run", "--no-sync", "python", "-m", "pytest", "-q"]), end="")
        for command in (["ruff", "check", "."], ["ruff", "format", "--check", "."], ["mypy"]):
            print(run(["uv", "run", "--no-sync", *command]), end="")
        print("PASS: uv.lock generated and locked environment verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--database", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--lock", action="store_true", help="also lock dependencies and rerun checks")
    args = parser.parse_args()
    try:
        if args.database:
            database_checks()
        else:
            host_checks(lock=args.lock)
    except Exception as exc:
        # Database/HTTP exceptions can carry DSNs or headers; print only safe failure context.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"FAIL: M0 verification stopped: {message}", file=sys.stderr)
        sys.exit(1)
