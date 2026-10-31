import asyncio
import os
import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from tests.unit.test_mcp import HEADERS, setup_mcp

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_HTTP_TESTS") != "1", reason="requires loopback sockets"
    ),
]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_official_sdk_over_real_http(mode):
    app, store, tools = setup_mcp()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        async def check():
            async with httpx2.AsyncClient(headers=HEADERS, trust_env=False, timeout=10) as http:
                async with Client(
                    streamable_http_client(f"http://127.0.0.1:{port}/mcp/", http_client=http),
                    mode=mode,
                ) as client:
                    listed = await client.list_tools()
                    assert len(listed.tools) == 3
                    result = await client.call_tool("mysql_health", {"instance_id": "mysql-lab-01"})
                    assert not result.is_error
                    evidence = result.structured_content
                    response = await http.get(
                        f"http://127.0.0.1:{port}/api/v1/diagnoses/{evidence['task_id']}/evidence"
                    )
                    assert response.json()[0]["evidence_id"] == evidence["evidence_id"]
                    bad = await client.call_tool(
                        "mysql_health", {"instance_id": "mysql-lab-01", "sql": "SELECT 1"}
                    )
                    assert bad.is_error
            assert tools.calls == ["mysql.health"]

        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()
