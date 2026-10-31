import re
import time
from datetime import datetime

from pydantic import JsonValue, TypeAdapter

from dbic.adapters.http import BackendFailure, ReadOnlyHTTP
from dbic.core.history import CATEGORIES, EVENT_SOURCE, request_window
from dbic.core.models import EvidenceStatus, ToolResult, UtcTime, utc_now

TIMESTAMP = TypeAdapter(UtcTime)


class ElasticSearchAdapter:
    def __init__(self, http: ReadOnlyHTTP) -> None:
        self.http = http

    async def search_events(
        self, *, instance_id: str, start: str, end: str, categories: list[str]
    ) -> ToolResult:
        started = time.monotonic()
        window = None

        def result(
            status: EvidenceStatus,
            *,
            payload: dict[str, JsonValue] | None = None,
            limitations: list[str] | None = None,
            observed_at: datetime | None = None,
        ) -> ToolResult:
            return ToolResult(
                status=status,
                source=EVENT_SOURCE,
                kind="log",
                window=window,
                observed_at=observed_at or utc_now(),
                payload=payload or {},
                limitations=limitations or [],
                duration_ms=(time.monotonic() - started) * 1000,
            )

        try:
            window = request_window(instance_id, start, end)
            if not categories or not set(categories) <= CATEGORIES or len(categories) > 3:
                raise ValueError("unsupported categories")
        except ValueError:
            return result("forbidden", limitations=["invalid_historical_request"])
        body = {
            "size": 51,
            "timeout": "3s",
            "track_total_hits": False,
            "_source": ["@timestamp", "db.type", "db.instance_id", "event.category", "event.code"],
            "sort": [{"@timestamp": "asc"}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"db.type": "mysql"}},
                        {"term": {"db.instance_id": instance_id}},
                        {"terms": {"event.category": categories}},
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": window.start.isoformat(),
                                    "lte": window.end.isoformat(),
                                }
                            }
                        },
                    ]
                }
            },
        }
        try:
            raw = await self.http.json(
                "POST",
                "/db-events-lab/_search",
                params={"allow_partial_search_results": "false"},
                body=body,
            )
            if raw.get("timed_out") is not False or raw["_shards"]["failed"] != 0:
                raise BackendFailure("unavailable", "incomplete_search_result")
            hits = raw["hits"]["hits"]
            if not isinstance(hits, list) or len(hits) > 51:
                raise ValueError("invalid hits")
            events: list[JsonValue] = []
            timestamps: list[datetime] = []
            for hit in hits:
                item = hit["_source"]
                observed = TIMESTAMP.validate_python(item["@timestamp"])
                category, code = item["event"]["category"], item["event"]["code"]
                if (
                    item["db"]["type"] != "mysql"
                    or item["db"]["instance_id"] != instance_id
                    or category not in categories
                    or not window.start <= observed <= window.end
                    or hit["_index"] != "db-events-lab"
                    or not isinstance(code, str)
                    or not re.fullmatch(r"[A-Z0-9_]{1,64}", code)
                    or not isinstance(hit["_id"], str)
                    or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", hit["_id"])
                ):
                    raise ValueError("event identity or fields invalid")
                timestamps.append(observed)
                events.append(
                    {
                        "at": observed.isoformat(),
                        "category": category,
                        "code": code,
                        "document_id": hit["_id"],
                        "index": "db-events-lab",
                    }
                )
            if timestamps != sorted(timestamps):
                raise ValueError("unordered events")
            return result(
                "ok" if events else "empty",
                payload={"events": events[:50]},
                limitations=["events_truncated_at_50"] if len(events) > 50 else [],
                observed_at=timestamps[min(len(timestamps), 50) - 1] if timestamps else None,
            )
        except BackendFailure as exc:
            code = "index_not_found" if exc.code == "http_404" else exc.code
            return result(exc.status, limitations=[code])
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            return result("error", limitations=["invalid_search_response:" + type(exc).__name__])

    async def similar_incidents(self, *, query: str, limit: int) -> ToolResult:
        return ToolResult(
            status="unavailable",
            source="search.incidents",
            kind="historical_incident",
            observed_at=utc_now(),
            duration_ms=0,
            limitations=["similar incident retrieval not implemented"],
        )
