import json
from pathlib import Path

import httpx
import pytest

from scripts.seed_elastic import seed

MAPPING = json.loads(Path("configs/elastic/db-events-lab.json").read_text())
SAMPLES = json.loads(Path("configs/elastic/events.sample.json").read_text())


def test_seed_create_only_and_idempotent():
    documents = {}
    created = []
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/db-events-lab/_mapping":
            return (
                httpx.Response(200, json={"db-events-lab": {"mappings": MAPPING["mappings"]}})
                if created
                else httpx.Response(404)
            )
        if path == "/db-events-lab" and request.method == "PUT":
            created.append(json.loads(request.content))
            return httpx.Response(200)
        document_id = path.rsplit("/", 1)[-1]
        if "/_create/" in path and request.method == "PUT":
            if document_id in documents:
                return httpx.Response(409)
            documents[document_id] = json.loads(request.content)
            return httpx.Response(201)
        assert "/_doc/" in path and request.method == "GET"
        return httpx.Response(200, json={"_source": documents[document_id]})

    with httpx.Client(
        base_url="http://localhost:9200", transport=httpx.MockTransport(handler)
    ) as client:
        seed(client, MAPPING, SAMPLES)
        seed(client, MAPPING, SAMPLES)
    assert created == [MAPPING] and len(documents) == 2
    assert all(
        method == "GET" or (method == "PUT" and (path == "/db-events-lab" or "/_create/" in path))
        for method, path in calls
    )


def test_foreign_mapping_collision_never_writes():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"db-events-lab": {"mappings": {"properties": {}}}})

    with httpx.Client(
        base_url="http://localhost:9200", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(RuntimeError, match="no changes made"):
            seed(client, MAPPING, SAMPLES)


def test_document_collision_is_not_overwritten():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/_mapping"):
            return httpx.Response(200, json={"db-events-lab": {"mappings": MAPPING["mappings"]}})
        if "/_create/" in request.url.path:
            return httpx.Response(409)
        return httpx.Response(200, json={"_source": {"unrelated": "data"}})

    with httpx.Client(
        base_url="http://localhost:9200", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(RuntimeError, match="not overwritten"):
            seed(client, MAPPING, SAMPLES)
    assert len([r for r in calls if r.method == "PUT"]) == 1
