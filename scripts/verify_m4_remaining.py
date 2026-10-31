"""Read-only PostgreSQL/Doris REST and MCP checks; no fault fixtures or maintenance."""

import argparse
import os
import subprocess
import sys
import time

from verify_m1 import COMPOSE, ROOT, request, require
from verify_m3 import rpc

PROBLEMS = {"postgresql": "maintenance_health", "doris": "cluster_health"}
SOURCES = {
    "postgresql": {"postgresql.blocking_sessions", "postgresql.vacuum_health"},
    "doris": {"doris.fe_be_health", "doris.query_profile_summary"},
}


def verify_item(item, kind, task_id):
    require(
        item["task_id"] == task_id
        and item["instance_id"] == kind + "-lab-01"
        and item["redacted"] is True
        and item["source"] in SOURCES[kind]
        and item["status"] in {"ok", "empty"},
        "invalid or unavailable diagnostic evidence",
    )
    payload, source = item["payload"], item["source"]
    if source == "doris.fe_be_health":
        require(
            payload == {"online_backends": 1, "total_backends": 1} and item["status"] == "ok",
            "Doris lab backend not online",
        )
        return
    key = {
        "postgresql.blocking_sessions": "sessions",
        "postgresql.vacuum_health": "tables",
        "doris.query_profile_summary": "samples",
    }[source]
    fields = {key, "truncated"} | ({"stats_reset_at"} if key == "tables" else set())
    require(
        set(payload) == fields
        and type(payload["truncated"]) is bool
        and isinstance(payload[key], list)
        and len(payload[key]) <= (20 if key == "samples" else 50),
        "invalid diagnostic sample shape",
    )
    require((item["status"] == "empty") == (not payload[key]), "inconsistent empty evidence")
    allowed = {
        "sessions": {"waiter_pid", "blocker_pids", "transaction_age_seconds"},
        "tables": {
            "relation_id",
            "live_tuples_estimate",
            "dead_tuples_estimate",
            "last_vacuum_at",
            "last_autovacuum_at",
            "vacuum_count",
            "autovacuum_count",
        },
        "samples": {"duration_ms", "state"},
    }[key]
    require(all(set(row) == allowed for row in payload[key]), "unexpected sample fields")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", choices=PROBLEMS, required=True)
    parser.add_argument("--require-mcp", action="store_true")
    args = parser.parse_args()
    kind = args.database
    os.chdir(ROOT)
    token = subprocess.run(
        COMPOSE
        + [
            "exec",
            "-T",
            "dbic-api",
            "python",
            "-c",
            "import os; print(os.environ['DBIC_API_TOKEN'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(request("/readyz") == (200, {"status": "ready"}), "schema not ready")
    code, caps = request("/api/v1/capabilities", token)
    require(code == 200 and caps["milestone"] == "M4", "deploy M4 first")
    capability = next(d for d in caps["databases"] if d["database_type"] == kind)
    require(capability["enabled"] and set(capability["tools"]) == SOURCES[kind], "lab not enabled")
    code, created = request(
        "/api/v1/diagnoses",
        token,
        {
            "instance_id": kind + "-lab-01",
            "problem_type": PROBLEMS[kind],
        },
    )
    require(code == 202, "diagnosis creation failed")
    path = "/api/v1/diagnoses/" + created["task_id"]
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        code, detail = request(path, token)
        require(code == 200, "task lookup failed")
        if detail["task"]["status"] not in {"queued", "running"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("diagnosis timed out")
    code, evidence = request(path + "/evidence", token)
    require(
        code == 200
        and len(evidence) == len(detail["steps"]) == 2
        and {e["source"] for e in evidence} == SOURCES[kind],
        "evidence set mismatch",
    )
    for item in evidence:
        verify_item(item, kind, created["task_id"])
    report = detail["report"]
    ids = {e["evidence_id"] for e in evidence}
    require(
        detail["task"]["status"] in {"partial", "completed"}
        and report["agent_status"] == "disabled"
        and not report["agent_suggestions"]
        and report["findings"]
        and all(f["evidence_ids"] and set(f["evidence_ids"]) <= ids for f in report["findings"]),
        "invalid rule report",
    )
    print(
        f"PASS: {kind} {PROBLEMS[kind]} status={detail['task']['status']}, "
        "evidence=2, agent=disabled"
    )
    if caps["mcp_enabled"]:
        for source in sorted(SOURCES[kind]):
            code, response = rpc(
                "tools/call",
                token,
                {"name": source.replace(".", "_"), "arguments": {"instance_id": kind + "-lab-01"}},
            )
            require(
                code == 200 and not response.get("result", {}).get("isError", True),
                "MCP call failed",
            )
            item = response["result"]["structuredContent"]
            code, archived = request("/api/v1/diagnoses/" + item["task_id"] + "/evidence", token)
            require(
                code == 200 and archived == [item] and item["source"] == source,
                "MCP archive mismatch",
            )
            verify_item(item, kind, item["task_id"])
            print(f"PASS: {source} MCP/REST evidence matches")
    else:
        require(not args.require_mcp, "enable MCP first")
        print("INFO: MCP skipped; disabled")
    print("PASS: read-only M4 verification; no fault fixtures executed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit("FAIL: M4 verification: " + message)
