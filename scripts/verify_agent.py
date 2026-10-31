"""Verify real Agent suggestions through the deployed API (two bounded model requests)."""

import os
import subprocess
import sys
import time

from verify_m1 import COMPOSE, ROOT, request, require


def check_report(detail, evidence):
    report = detail["report"]
    require(detail["task"]["status"] in {"completed", "partial"}, "diagnosis did not complete")
    require(
        report["agent_status"] == "ok",
        "agent validation failed: "
        + report["agent_status"]
        + "/"
        + str(report.get("agent_reason")),
    )
    ids = {item["evidence_id"] for item in evidence}
    require(len(detail["steps"]) == len(evidence) == 2, "agent executed unexpected extra tools")
    suggestions = report["agent_suggestions"]
    require(len(suggestions) <= 3, "agent exceeded suggestion budget")
    for item in suggestions:
        require(
            item["instance_id"] == "mysql-lab-01"
            and item["tool_id"] in {"mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"}
            and item["evidence_ids"]
            and set(item["evidence_ids"]) <= ids,
            "agent suggestion target or evidence mismatch",
        )
    return len(suggestions)


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
    code, capabilities = request("/api/v1/capabilities", token)
    require(
        code == 200 and capabilities["agent_backend"] == "openai_compatible",
        "enable the agent backend first",
    )
    for problem in ("lock_wait", "slow_query"):
        code, task = request(
            "/api/v1/diagnoses", token, {"instance_id": "mysql-lab-01", "problem_type": problem}
        )
        require(code == 202, "diagnosis creation failed")
        path = "/api/v1/diagnoses/" + task["task_id"]
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            code, detail = request(path, token)
            require(code == 200, "task lookup failed")
            if detail["task"]["status"] not in {"queued", "running"}:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("agent task timed out")
        code, evidence = request(path + "/evidence", token)
        require(code == 200, "evidence lookup failed")
        count = check_report(detail, evidence)
        print(
            f"PASS: {problem} agent_status=ok, suggestions={count}, persisted evidence=2",
            flush=True,
        )
    print("PASS: real Agent API validation; no suggested tool was automatically executed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit("FAIL: Agent verification: " + message)
