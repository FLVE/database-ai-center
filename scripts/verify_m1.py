"""Verify the running M1 API, then run MySQL fixtures on the isolated verify network."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "deploy/compose.yaml"]


def request(path, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        "http://127.0.0.1:8086" + path,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def main():
    os.chdir(ROOT)
    result = subprocess.run(
        COMPOSE
        + [
            "exec",
            "-T",
            "dbic-api",
            "python",
            "-c",
            "import os; print(os.environ['DBIC_API_TOKEN'])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    token = result.stdout.strip()
    require(request("/readyz") == (200, {"status": "ready"}), "M1 schema not ready")
    require(request("/api/v1/capabilities")[0] == 401, "anonymous access was accepted")
    require(request("/api/v1/capabilities", "invalid")[0] == 401, "wrong token was accepted")
    status, capabilities = request("/api/v1/capabilities", token)
    require(
        status == 200 and capabilities["milestone"] in {"M1", "M2"}, "M1+ image is not deployed"
    )
    for problem in ("lock_wait", "slow_query"):
        status, created = request(
            "/api/v1/diagnoses", token, {"instance_id": "mysql-lab-01", "problem_type": problem}
        )
        require(status == 202, "diagnosis creation failed")
        path = "/api/v1/diagnoses/" + created["task_id"]
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            status, detail = request(path, token)
            require(status == 200, "task read failed")
            if detail["task"]["status"] not in ("queued", "running"):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("diagnosis did not reach a terminal state")
        require(detail["task"]["status"] in ("completed", "partial"), "diagnosis failed")
        status, evidence = request(path + "/evidence", token)
        require(status == 200 and len(evidence) == 2, "evidence was not persisted")
        require(evidence[0]["status"] == "ok", "mysql health failed; check diag account setup")
        require(
            all(e["status"] not in ("forbidden", "error", "timeout") for e in evidence),
            "diagnostic account/tool failed",
        )
        ids = {e["evidence_id"] for e in evidence}
        require(
            all(set(f["evidence_ids"]) <= ids for f in detail["report"]["findings"]),
            "report evidence references are invalid",
        )
        require(
            request(path + "/feedback", token, {"verdict": "partial", "comment": "M1 smoke test"})[
                0
            ]
            == 201,
            "feedback was not persisted",
        )
        print(
            f"PASS: live {problem} status={detail['task']['status']}, evidence/report/feedback",
            flush=True,
        )
    print(
        "Starting isolated test databases; running service databases are not test targets",
        flush=True,
    )
    subprocess.run(
        COMPOSE + ["--profile", "verify", "run", "--build", "--rm", "dbic-tests"], check=True
    )
    print("PASS: M1 live API and isolated database test suite")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit(f"FAIL: M1 verification: {message}")
