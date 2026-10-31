"""Preflight or deploy a fresh, isolated Linux lab; never reuse existing DBIC resources."""

import argparse
import os
import platform
import secrets
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ".env",
    "-f",
    "deploy/compose.yaml",
    "-f",
    "deploy/compose.linux.yaml",
]


def capture(command):
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def preflight():
    # Bootstrap runs with the host interpreter before project dependencies are installed.
    if sys.version_info < (3, 11):  # noqa: UP036
        raise RuntimeError(
            "host verification scripts require Python 3.11+; API image uses Python 3.12"
        )
    if platform.system() != "Linux":
        raise RuntimeError("this installer is for the target Linux host only")
    endpoint = capture(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
    if not endpoint.startswith("unix://") or not os.environ.get(
        "DOCKER_HOST", "unix://"
    ).startswith("unix://"):
        raise RuntimeError("deployment requires the target host's local Docker Unix socket")
    print("Host architecture: " + platform.machine(), flush=True)
    print(capture(["docker", "compose", "version"]), flush=True)
    print(
        "Docker server: " + capture(["docker", "version", "--format", "{{.Server.Version}}"]),
        flush=True,
    )
    for kind, args in [
        ("container", ["docker", "ps", "-aq"]),
        ("volume", ["docker", "volume", "ls", "-q"]),
        ("network", ["docker", "network", "ls", "-q"]),
    ]:
        if capture(args + ["--filter", "label=com.docker.compose.project=dbic-lab"]):
            raise RuntimeError(f"existing dbic-lab {kind} found; inspect before any deployment")
    if os.path.lexists(ROOT / ".env"):
        raise RuntimeError("existing .env found; fresh installer will not overwrite or reuse it")
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 8086))
    except OSError as exc:
        raise RuntimeError("loopback port 8086 is unavailable; no deployment started") from exc
    print("Existing containers (names/images/ports only):", flush=True)
    print(capture(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Ports}}"]), flush=True)
    print("PASS: fresh lab preflight; no containers or existing configuration changed", flush=True)


def initialize_env(path):
    values = {"DBIC_ENV": "lab", "DBIC_SEARCH_BACKEND": "null", "DBIC_METRICS_BACKEND": "null"}
    for key in ("DBIC_PG_PASSWORD", "DBIC_API_TOKEN", "MYSQL_ROOT_PASSWORD", "MYSQL_DIAG_PASSWORD"):
        values[key] = secrets.token_hex(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))


def deploy():
    initialize_env(ROOT / ".env")
    subprocess.run(COMPOSE + ["config", "--quiet"], check=True)
    subprocess.run(
        COMPOSE + ["up", "-d", "--wait", "--wait-timeout", "180", "dbic-postgres", "mysql-lab"],
        check=True,
    )
    subprocess.run([sys.executable, "scripts/setup_mysql_lab.py"], check=True)
    subprocess.run(
        COMPOSE + ["up", "-d", "--build", "--wait", "--wait-timeout", "180", "dbic-api"], check=True
    )
    subprocess.run([sys.executable, "scripts/verify_m2.py"], check=True)
    print("PASS: Linux lab deployed and verified with Null history; ES verification still pending")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="create fresh secrets, start only DBIC lab and run verification",
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    # Shell-exported settings must not silently override the fresh lab's generated .env.
    if any(key.startswith(("DBIC_", "MYSQL_", "COMPOSE_")) for key in os.environ):
        raise RuntimeError("unset inherited DBIC_ / MYSQL_ / COMPOSE_ variables before deployment")
    preflight()
    if args.deploy:
        deploy()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(
            f"FAIL: Linux lab deployment: {message}; no automatic cleanup performed"
        ) from None
