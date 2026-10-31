"""Read-only deployed Redis lab verification; never seeds data or modifies Redis ACLs."""

import argparse
import os
import subprocess
import sys
import time

from verify_m1 import COMPOSE, ROOT, request, require
from verify_m3 import rpc

SOURCES = {"redis.memory_breakdown", "redis.slowlog_summary", "redis.bigkey_audit_read"}


def verify_item(item, task_id, require_audit):
    require(
        item["task_id"] == task_id and item["instance_id"] == "redis-lab-01" and item["redacted"],
        "invalid evidence ownership/redaction",
    )
    if item["source"] == "redis.memory_breakdown":
        require(
            item["status"] == "ok" and type(item["payload"].get("used_memory_bytes")) is int,
            "Redis memory source failed",
        )
    elif item["source"] == "redis.slowlog_summary":
        require(item["status"] in {"ok", "empty"}, "Redis slowlog source failed")
    elif item["source"] == "redis.bigkey_audit_read":
        require(
            item["status"] == "ok"
            or (not require_audit and item["status"] == "unavailable" and item["limitations"]),
            "Redis audit required or invalid",
        )
    else:
        raise RuntimeError("unexpected Redis evidence source")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-audit", action="store_true")
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
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    require(request("/readyz") == (200, {"status": "ready"}), "schema not ready")
    status, capabilities = request("/api/v1/capabilities", token)
    require(status == 200 and capabilities["milestone"] == "M4", "deploy M4.1 first")
    redis = next(d for d in capabilities["databases"] if d["database_type"] == "redis")
    require(
        redis["enabled"] and set(redis["tools"]) == SOURCES, "enable Redis lab before verification"
    )
    status, created = request(
        "/api/v1/diagnoses",
        token,
        {"instance_id": "redis-lab-01", "problem_type": "memory_pressure"},
    )
    require(status == 202, "Redis diagnosis creation failed")
    path = "/api/v1/diagnoses/" + created["task_id"]
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        code, detail = request(path, token)
        require(code == 200, "task lookup failed")
        if detail["task"]["status"] not in {"queued", "running"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Redis task timed out")
    require(detail["task"]["status"] in {"partial", "completed"}, "Redis task failed")
    code, evidence = request(path + "/evidence", token)
    require(code == 200 and len(detail["steps"]) == 3, "evidence persistence failed")
    require(
        {e["source"] for e in evidence} == SOURCES and len(evidence) == 3,
        "unexpected Redis evidence set",
    )
    for item in evidence:
        verify_item(item, created["task_id"], args.require_audit)
    report = detail["report"]
    ids = {e["evidence_id"] for e in evidence}
    require(
        report["agent_status"] == "disabled" and not report["agent_suggestions"],
        "Redis unexpectedly used model",
    )
    require(
        report["findings"]
        and all(f["evidence_ids"] and set(f["evidence_ids"]) <= ids for f in report["findings"]),
        "invalid report references",
    )
    audit = next(e for e in evidence if e["source"] == "redis.bigkey_audit_read")
    print(
        f"PASS: Redis memory_pressure status={detail['task']['status']}, "
        f"evidence=3, agent=disabled, audit={audit['status']}",
        flush=True,
    )
    if not capabilities["mcp_enabled"]:
        require(not args.require_mcp, "enable MCP before verification")
        print("INFO: MCP disabled; Redis MCP verification skipped")
    else:
        for source in sorted(SOURCES):
            status, response = rpc(
                "tools/call",
                token,
                {"name": source.replace(".", "_"), "arguments": {"instance_id": "redis-lab-01"}},
            )
            require(
                status == 200 and not response.get("result", {}).get("isError", True),
                "Redis MCP call failed",
            )
            item = response["result"]["structuredContent"]
            code, archived = request("/api/v1/diagnoses/" + item["task_id"] + "/evidence", token)
            require(
                code == 200 and archived == [item] and item["source"] == source,
                "Redis MCP archive mismatch",
            )
            verify_item(item, item["task_id"], args.require_audit)
            print(f"PASS: {source} MCP/REST evidence matches", flush=True)
    print("PASS: M4.1 live read-only verification; no fault fixtures executed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit("FAIL: M4.1 verification: " + message)
