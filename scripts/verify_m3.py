"""Verify M3 capabilities and authenticated MCP against the local deployed lab API."""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

from verify_m1 import COMPOSE, ROOT, request, require


def rpc(method, token, params=None):
    req = urllib.request.Request(
        "http://127.0.0.1:8086/mcp/",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        ).encode(),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=100)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-mcp", action="store_true")
    args = parser.parse_args()
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
    require(request("/readyz") == (200, {"status": "ready"}), "database schema not ready")
    status, capabilities = request("/api/v1/capabilities", token)
    require(status == 200 and capabilities["milestone"] in {"M3", "M4"}, "deploy M3+ first")
    print("PASS: M3 capabilities and readiness", flush=True)
    if not capabilities["mcp_enabled"]:
        require(not args.require_mcp, "enable DBIC_MCP_ENABLED and recreate API")
        require(rpc("tools/list", token)[0] == 404, "disabled MCP should not be available")
        print("PASS: MCP disabled; no real model validation performed")
        return
    for invalid in ("", "wrong"):
        require(rpc("tools/list", invalid)[0] == 401, "MCP accepted an invalid token")
    status, initialized = rpc(
        "initialize",
        token,
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "dbic-verify", "version": "0.4.0"},
        },
    )
    require(status == 200 and "result" in initialized, "MCP initialization failed")
    status, listed = rpc("tools/list", token)
    names = {"mysql_health", "mysql_lock_chain", "mysql_slow_query_summary"}
    expected = names | {
        tool.replace(".", "_")
        for db in capabilities["databases"]
        if db["database_type"] == "redis"
        for tool in db["tools"]
    }
    require(
        status == 200 and {t["name"] for t in listed["result"]["tools"]} == expected,
        "MCP tool allowlist mismatch",
    )
    for name in sorted(names):
        status, response = rpc(
            "tools/call", token, {"name": name, "arguments": {"instance_id": "mysql-lab-01"}}
        )
        require(
            status == 200 and not response.get("result", {}).get("isError", True),
            "MCP call failed: " + name,
        )
        item = response["result"]["structuredContent"]
        path = "/api/v1/diagnoses/" + item["task_id"]
        code, detail = request(path, token)
        require(
            code == 200 and detail["task"]["requested_by"] == "mcp-local-user",
            "MCP task not readable through REST",
        )
        code, evidence = request(path + "/evidence", token)
        require(
            code == 200 and len(evidence) == 1 and evidence[0] == item and item["redacted"],
            "MCP evidence persistence mismatch",
        )
        if name == "mysql_health":
            require(item["status"] == "ok", "MySQL health failed")
        elif name == "mysql_lock_chain":
            require(item["status"] in {"ok", "empty"}, "MySQL lock lookup failed")
        else:
            from verify_m2 import verify_realtime

            health = {"source": "mysql.health", "status": "ok"}
            verify_realtime("slow_query", [health, item], detail)
        print(f"PASS: {name} status={item['status']}; REST evidence matches", flush=True)
    _, denied = rpc(
        "tools/call",
        token,
        {"name": "mysql_health", "arguments": {"instance_id": "mysql-lab-01", "sql": "SELECT 1"}},
    )
    require(
        "error" in denied or denied.get("result", {}).get("isError"),
        "MCP accepted arbitrary SQL argument",
    )
    print("PASS: M3 MCP verification; no real model validation performed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        sys.exit("FAIL: M3 verification: " + message)
