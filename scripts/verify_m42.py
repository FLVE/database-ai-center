"""Read-only MongoDB lab API/MCP verification; never creates database fault fixtures."""

import argparse
import os
import subprocess
import sys
import time

from verify_m1 import COMPOSE, ROOT, request, require
from verify_m3 import rpc

SOURCES = {"mongodb.current_ops_summary", "mongodb.replica_health"}


def verify_item(item, task_id):
    require(
        item["task_id"] == task_id
        and item["instance_id"] == "mongodb-lab-01"
        and item["redacted"]
        and item["source"] in SOURCES,
        "invalid MongoDB evidence ownership",
    )
    payload = item["payload"]
    if item["source"] == "mongodb.current_ops_summary":
        require(
            item["status"] in {"ok", "empty"} and set(payload) == {"samples", "truncated"},
            "MongoDB current operations failed",
        )
        require(
            len(payload["samples"]) <= 50 and type(payload["truncated"]) is bool,
            "invalid operation sample limits",
        )
        require(
            (item["status"] == "empty") == (len(payload["samples"]) == 0),
            "inconsistent operation status",
        )
        for sample in payload["samples"]:
            require(
                set(sample) == {"operation", "running_seconds", "waiting_for_lock"},
                "unexpected operation fields",
            )
    else:
        require(
            item["status"] == "ok" and payload.get("local_state") == "PRIMARY",
            "MongoDB lab primary not observed",
        )
        members = payload.get("members", [])
        require(
            len(members) == 1
            and members[0].get("is_self") is True
            and members[0].get("reachable") is True
            and members[0].get("state") == "PRIMARY",
            "MongoDB member state invalid",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-mcp", action="store_true")
    args = parser.parse_args()
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
    code, capabilities = request("/api/v1/capabilities", token)
    require(code == 200 and capabilities["milestone"] == "M4", "deploy M4.2 first")
    mongo = next(d for d in capabilities["databases"] if d["database_type"] == "mongodb")
    require(mongo["enabled"] and set(mongo["tools"]) == SOURCES, "enable MongoDB lab first")
    code, created = request(
        "/api/v1/diagnoses",
        token,
        {"instance_id": "mongodb-lab-01", "problem_type": "activity_health"},
    )
    require(code == 202, "MongoDB diagnosis creation failed")
    path = "/api/v1/diagnoses/" + created["task_id"]
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        code, detail = request(path, token)
        require(code == 200, "task lookup failed")
        if detail["task"]["status"] not in {"queued", "running"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("MongoDB task timed out")
    code, evidence = request(path + "/evidence", token)
    require(
        code == 200
        and len(evidence) == len(detail["steps"]) == 2
        and {e["source"] for e in evidence} == SOURCES,
        "MongoDB evidence set mismatch",
    )
    require(detail["task"]["status"] in {"partial", "completed"}, "MongoDB task failed")
    for item in evidence:
        verify_item(item, created["task_id"])
    report = detail["report"]
    require(
        report["agent_status"] == "disabled" and not report["agent_suggestions"],
        "MongoDB unexpectedly used model",
    )
    ids = {e["evidence_id"] for e in evidence}
    require(
        report["findings"]
        and all(f["evidence_ids"] and set(f["evidence_ids"]) <= ids for f in report["findings"]),
        "invalid report evidence references",
    )
    print(
        f"PASS: MongoDB activity_health status={detail['task']['status']}, "
        "evidence=2, agent=disabled",
        flush=True,
    )
    if capabilities["mcp_enabled"]:
        for source in sorted(SOURCES):
            code, response = rpc(
                "tools/call",
                token,
                {"name": source.replace(".", "_"), "arguments": {"instance_id": "mongodb-lab-01"}},
            )
            require(
                code == 200 and not response.get("result", {}).get("isError", True),
                "MongoDB MCP call failed",
            )
            item = response["result"]["structuredContent"]
            code, archived = request("/api/v1/diagnoses/" + item["task_id"] + "/evidence", token)
            require(
                code == 200 and archived == [item] and item["source"] == source,
                "MongoDB MCP archive mismatch",
            )
            verify_item(item, item["task_id"])
            print(f"PASS: {source} MCP/REST evidence matches", flush=True)
    else:
        require(not args.require_mcp, "enable MCP before verification")
        print("INFO: MongoDB MCP verification skipped; MCP disabled")
    print("PASS: M4.2 live read-only verification; no fault fixtures executed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit("FAIL: M4.2 verification: " + message)
