from dbic.core.history import METRIC_SOURCES, request_window
from dbic.core.models import ToolResult, utc_now


class NullMetrics:
    async def query_range(
        self, *, instance_id: str, metric_key: str, start: str, end: str
    ) -> ToolResult:
        window = None
        valid = True
        try:
            window = request_window(instance_id, start, end)
            if metric_key not in METRIC_SOURCES:
                raise ValueError("unsupported metric")
        except ValueError:
            valid = False
        return ToolResult(
            status="unavailable" if valid else "forbidden",
            source=METRIC_SOURCES.get(metric_key, "metrics.unsupported"),
            kind="metric",
            window=window,
            observed_at=utc_now(),
            duration_ms=0,
            limitations=[
                "historical metrics backend is disabled" if valid else "invalid_historical_request"
            ],
        )
