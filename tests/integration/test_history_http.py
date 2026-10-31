"""Exercise real sockets in the isolated verification container; no external backend needed."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dbic.adapters.http import ReadOnlyHTTP
from dbic.adapters.metrics.prometheus import PrometheusMetricsAdapter
from dbic.adapters.search.elastic import ElasticSearchAdapter
from tests.unit.test_history_adapters import matrix, query, search_response

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("DBIC_HTTP_TESTS") != "1", reason="requires loopback socket permission"
    ),
]


@pytest.mark.parametrize("kind", ["metric", "log"])
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("ok", "ok"),
        ("401", "forbidden"),
        ("403", "forbidden"),
        ("503", "unavailable"),
        ("disconnect", "unavailable"),
        ("timeout", "timeout"),
    ],
)
def test_real_http_failure_modes(kind, scenario, expected):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.command, self.path, body))
            if scenario == "disconnect":
                self.close_connection = True
                return
            if scenario == "timeout":
                time.sleep(5.2)
                self.close_connection = True
                return
            status = 200 if scenario == "ok" else int(scenario)
            payload = matrix() if kind == "metric" else search_response()
            data = json.dumps(payload if status == 200 else {"error": "secret-upstream"}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        http = ReadOnlyHTTP(f"http://127.0.0.1:{server.server_port}")
        adapter = (
            PrometheusMetricsAdapter(
                http, instance_label="instance", instance_value="mysql-lab:9104"
            )
            if kind == "metric"
            else ElasticSearchAdapter(http)
        )
        result = query(adapter, kind)
        assert result.status == expected
        assert "secret-upstream" not in result.model_dump_json()
        assert len(calls) == 1
        method, path, body = calls[0]
        assert method == ("GET" if kind == "metric" else "POST")
        assert path.startswith(
            "/api/v1/query_range?" if kind == "metric" else "/db-events-lab/_search?"
        )
        if kind == "log":
            assert json.loads(body)["size"] == 51
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
