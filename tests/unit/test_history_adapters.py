import asyncio
import copy
import json
from datetime import datetime

import httpx
import pytest

from dbic.adapters.history_factory import historical_adapters
from dbic.adapters.http import ReadOnlyHTTP, validate_endpoint
from dbic.adapters.metrics.prometheus import PrometheusMetricsAdapter
from dbic.adapters.search.elastic import ElasticSearchAdapter
from dbic.settings import Settings
from tests.unit.test_api import TOKEN

WINDOW = {"start": "2020-01-01T00:00:00Z", "end": "2020-01-01T00:00:02Z"}
STAMP = datetime.fromisoformat(WINDOW["start"]).timestamp()
INSTANCE = "mysql-lab-01"


def matrix(values=None):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"instance": "mysql-lab:9104", "private_label": "do-not-return"},
                    "values": values
                    if values is not None
                    else [[STAMP + i, "3"] for i in range(3)],
                }
            ],
        },
    }


def search_response(count=1):
    return {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {
            "hits": [
                {
                    "_index": "db-events-lab",
                    "_id": f"sample-{i}",
                    "_source": {
                        "@timestamp": WINDOW["start"],
                        "db": {"type": "mysql", "instance_id": INSTANCE},
                        "event": {"category": "deadlock", "code": "ER_LOCK_DEADLOCK"},
                        "message": "password=do-not-return SELECT sensitive FROM accounts",
                    },
                }
                for i in range(count)
            ]
        },
    }


def adapters(handler):
    http = ReadOnlyHTTP("http://127.0.0.1:19090", transport=httpx.MockTransport(handler))
    return (
        PrometheusMetricsAdapter(http, instance_label="instance", instance_value="mysql-lab:9104"),
        ElasticSearchAdapter(http),
    )


def query(adapter, kind, **overrides):
    arguments = {"instance_id": INSTANCE, **WINDOW}
    if kind == "metric":
        return asyncio.run(
            adapter.query_range(**(arguments | {"metric_key": "mysql.connections"} | overrides))
        )
    return asyncio.run(
        adapter.search_events(**(arguments | {"categories": ["deadlock", "error"]} | overrides))
    )


def test_predefined_metric_request_and_numeric_utc_output():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=matrix())

    metrics, _ = adapters(handler)
    result = query(metrics, "metric")
    request = requests[0]
    assert request.method == "GET" and request.url.path == "/api/v1/query_range"
    assert (
        request.url.params["query"]
        == 'mysql_global_status_threads_connected{instance="mysql-lab:9104"}'
    )
    assert request.url.params["step"] == "1"
    assert result.status == "ok" and not result.limitations
    assert result.payload["unit"] == "connections"
    assert result.payload["samples"][0] == {"at": "2020-01-01T00:00:00+00:00", "value": 3.0}
    assert result.observed_at.timestamp() == STAMP + 2
    assert "do-not-return" not in result.model_dump_json()
    query(metrics, "metric", metric_key="mysql.slow_queries")
    assert (
        requests[-1].url.params["query"]
        == 'rate(mysql_global_status_slow_queries{instance="mysql-lab:9104"}[5m])'
    )


def test_fixed_search_filters_drop_raw_logs_and_queries():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=search_response())

    _, search = adapters(handler)
    result = query(search, "log")
    request = requests[0]
    assert request.method == "POST" and request.url.path == "/db-events-lab/_search"
    assert request.url.params["allow_partial_search_results"] == "false"
    body = json.loads(request.content)
    assert body["size"] == 51 and body["timeout"] == "3s"
    assert "message" not in body["_source"]
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"db.instance_id": INSTANCE}} in filters
    assert filters[-1]["range"]["@timestamp"] == {
        "gte": "2020-01-01T00:00:00+00:00",
        "lte": "2020-01-01T00:00:02+00:00",
    }
    assert result.status == "ok" and result.kind == "log"
    assert result.payload["events"][0]["document_id"] == "sample-0"
    assert all(
        value not in result.model_dump_json() for value in ("do-not-return", "SELECT", "bool")
    )


@pytest.mark.parametrize("kind", ["metric", "log"])
@pytest.mark.parametrize(
    "code,status",
    [
        (401, "forbidden"),
        (403, "forbidden"),
        (404, "unavailable"),
        (429, "unavailable"),
        (500, "unavailable"),
        (503, "unavailable"),
        (302, "error"),
    ],
)
def test_http_failures_are_explicit_and_sanitized(kind, code, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            code, text="password=do-not-return", headers={"Location": "https://production.invalid"}
        )

    pair = adapters(handler)
    result = query(pair[0 if kind == "metric" else 1], kind)
    assert result.status == status and result.payload == {}
    assert result.window is not None and result.limitations
    assert "do-not-return" not in result.model_dump_json()
    assert len(calls) == 1
    if kind == "log" and code == 404:
        assert result.limitations == ["index_not_found"]


@pytest.mark.parametrize("kind", ["metric", "log"])
@pytest.mark.parametrize(
    "error,status", [(httpx.ConnectError, "unavailable"), (httpx.ReadTimeout, "timeout")]
)
def test_connection_and_timeout(kind, error, status):
    def handler(request):
        raise error("private host and token", request=request)

    pair = adapters(handler)
    result = query(pair[0 if kind == "metric" else 1], kind)
    assert result.status == status and not result.payload
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["metric", "log"])
@pytest.mark.parametrize(
    "content,code",
    [
        (b"not-json secret", "invalid_backend_response"),
        (b"[]", "invalid_backend_response"),
        (b"x" * 262145, "response_size_limit"),
    ],
)
def test_bounded_json_responses(kind, content, code):
    pair = adapters(lambda _: httpx.Response(200, content=content))
    result = query(pair[0 if kind == "metric" else 1], kind)
    assert result.status == "error" and result.limitations == [code]


@pytest.mark.parametrize(
    "values,status,limit",
    [
        ([], "unavailable", "metric_samples_missing"),
        ([[STAMP, "1"], [STAMP + 2, "2"]], "ok", "metric_sample_gaps"),
        ([[STAMP, "NaN"]], "error", None),
        ([[STAMP, "Inf"]], "error", None),
        ([[STAMP, "-1"]], "error", None),
        ([[STAMP - 1, "1"]], "error", None),
        ([[STAMP + 3, "1"]], "error", None),
        ([[STAMP + 0.5, "1"]], "error", None),
        ([[STAMP, "1"], [STAMP, "2"]], "error", None),
        ([[STAMP + 2, "1"], [STAMP, "2"]], "error", None),
        ([[STAMP, "1"]] * 302, "error", None),
    ],
)
def test_metric_sample_integrity(values, status, limit):
    metrics, _ = adapters(lambda _: httpx.Response(200, json=matrix(values)))
    result = query(metrics, "metric")
    assert result.status == status
    if limit:
        assert limit in result.limitations


def test_ambiguous_series_and_backend_warnings():
    response = matrix()
    response["data"]["result"].append(copy.deepcopy(response["data"]["result"][0]))
    metrics, _ = adapters(lambda _: httpx.Response(200, json=response))
    assert query(metrics, "metric").status == "error"
    response["data"]["result"].pop()
    response["warnings"] = ["private-label-value"]
    result = query(metrics, "metric")
    assert "backend_reported_query_limitations" in result.limitations
    assert "private-label-value" not in result.model_dump_json()
    response["data"]["result"] = []
    assert query(metrics, "metric").status == "unavailable"


@pytest.mark.parametrize(
    "count,status,limit",
    [(0, "empty", []), (50, "ok", []), (51, "ok", ["events_truncated_at_50"]), (52, "error", None)],
)
def test_event_count_and_truncation(count, status, limit):
    _, search = adapters(lambda _: httpx.Response(200, json=search_response(count)))
    result = query(search, "log")
    assert result.status == status
    if limit is not None:
        assert result.limitations == limit
        assert len(result.payload["events"]) == min(count, 50)


@pytest.mark.parametrize(
    "fault",
    [
        "partial_shards",
        "timed_out",
        "outside_window",
        "wrong_instance",
        "wrong_index",
        "unsafe_code",
        "naive_time",
    ],
)
def test_search_rejects_incomplete_or_untrusted_data(fault):
    response = search_response()
    hit = response["hits"]["hits"][0]
    if fault == "partial_shards":
        response["_shards"]["failed"] = 1
    elif fault == "timed_out":
        response["timed_out"] = True
    elif fault == "outside_window":
        hit["_source"]["@timestamp"] = "2019-12-31T23:59:59Z"
    elif fault == "wrong_instance":
        hit["_source"]["db"]["instance_id"] = "production"
    elif fault == "wrong_index":
        hit["_index"] = "production-events"
    elif fault == "unsafe_code":
        hit["_source"]["event"]["code"] = "secret sql text"
    else:
        hit["_source"]["@timestamp"] = "2020-01-01T00:00:00"
    _, search = adapters(lambda _: httpx.Response(200, json=response))
    result = query(search, "log")
    assert result.status in {"error", "unavailable"} and not result.payload


@pytest.mark.parametrize("kind", ["metric", "log"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"instance_id": "production"},
        {"start": "2020-01-01"},
        {"end": "2020-01-03T00:00:00Z"},
        {"end": "2999-01-01T00:00:00Z"},
    ],
)
def test_invalid_requests_make_no_http_calls(kind, overrides):
    def handler(request):
        pytest.fail("invalid input must not reach HTTP")

    pair = adapters(handler)
    assert query(pair[0 if kind == "metric" else 1], kind, **overrides).status == "forbidden"


def test_raw_queries_are_not_accepted():
    def handler(request):
        pytest.fail("arbitrary query must not reach HTTP")

    metrics, search = adapters(handler)
    assert query(metrics, "metric", metric_key="up{job=~'.*'}").status == "forbidden"
    assert query(search, "log", categories=["*"]).status == "forbidden"


@pytest.mark.parametrize(
    "url",
    [
        "http://production:9200",
        "http://localhost",
        "http://user:secret@localhost:9200",
        "http://localhost:9200/path",
        "http://localhost:9200?proxy=elsewhere",
        "file:///tmp/events",
        "http://localhost:9200#frag",
    ],
)
def test_only_explicit_local_endpoints(url):
    with pytest.raises(ValueError):
        validate_endpoint(url)


def test_factory_requires_explicit_lab_mapping_and_hides_secrets():
    base = {"api_token": TOKEN, "pg_password": "test-only"}
    with pytest.raises(ValueError):
        Settings(**base, metrics_backend="prometheus", prometheus_url="http://localhost:9090")
    config = Settings(
        **base,
        metrics_backend="prometheus",
        search_backend="elastic",
        prometheus_url="http://localhost:9090",
        elastic_url="http://localhost:9200",
        prometheus_instance_value="mysql-lab:9104",
        prometheus_token="metric-secret",
        elastic_api_key="search-secret",
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=matrix() if request.method == "GET" else search_response())

    metrics, search = historical_adapters(config, transport=httpx.MockTransport(handler))
    assert query(metrics, "metric").status == query(search, "log").status == "ok"
    assert calls[0].headers["Authorization"] == "Bearer metric-secret"
    assert calls[1].headers["Authorization"] == "ApiKey search-secret"
    assert "metric-secret" not in repr(config) and "search-secret" not in repr(config)
