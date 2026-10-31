from dbic.core.history import CATEGORIES, EVENT_SOURCE, request_window
from dbic.core.models import ToolResult, utc_now


class NullSearch:
    async def search_events(
        self, *, instance_id: str, start: str, end: str, categories: list[str]
    ) -> ToolResult:
        window = None
        valid = True
        try:
            window = request_window(instance_id, start, end)
            if not categories or not set(categories) <= CATEGORIES or len(categories) > 3:
                raise ValueError("unsupported categories")
        except ValueError:
            valid = False
        return ToolResult(
            status="unavailable" if valid else "forbidden",
            source=EVENT_SOURCE,
            kind="log",
            window=window,
            observed_at=utc_now(),
            duration_ms=0,
            limitations=[
                "historical search backend is disabled" if valid else "invalid_historical_request"
            ],
        )

    async def similar_incidents(self, *, query: str, limit: int) -> ToolResult:
        return ToolResult(
            status="unavailable",
            source="search.incidents",
            kind="historical_incident",
            observed_at=utc_now(),
            duration_ms=0,
            limitations=["incident search backend is disabled"],
        )
