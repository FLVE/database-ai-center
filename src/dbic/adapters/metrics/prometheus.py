import json
import math
import re
import time
from datetime import UTC, datetime

from pydantic import JsonValue

from dbic.adapters.http import BackendFailure, ReadOnlyHTTP
from dbic.core.history import METRIC_SOURCES, request_window
from dbic.core.models import EvidenceStatus, ToolResult, utc_now


class PrometheusMetricsAdapter:
    def __init__(self, http: ReadOnlyHTTP, *, instance_label: str, instance_value: str) -> None:
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", instance_label):
            raise ValueError("invalid Prometheus instance label")
        if not instance_value or len(instance_value) > 256:
            raise ValueError("explicit Prometheus instance value required")
        self.http, self.label, self.value = http, instance_label, instance_value

    async def query_range(
        self, *, instance_id: str, metric_key: str, start: str, end: str
    ) -> ToolResult:
        started = time.monotonic()
        source = METRIC_SOURCES.get(metric_key, "metrics.unsupported")
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
                source=source,
                kind="metric",
                window=window,
                observed_at=observed_at or utc_now(),
                payload=payload or {},
                limitations=limitations or [],
                duration_ms=(time.monotonic() - started) * 1000,
            )

        try:
            window = request_window(instance_id, start, end)
            if metric_key not in METRIC_SOURCES:
                raise ValueError("unsupported metric")
        except ValueError:
            return result("forbidden", limitations=["invalid_historical_request"])
        step = max(1, math.ceil((window.end - window.start).total_seconds() / 300))
        selector = "{" + self.label + "=" + json.dumps(self.value) + "}"
        query = (
            "mysql_global_status_threads_connected" + selector
            if metric_key == "mysql.connections"
            else "rate(mysql_global_status_slow_queries" + selector + "[5m])"
        )
        try:
            raw = await self.http.json(
                "GET",
                "/api/v1/query_range",
                params={
                    "query": query,
                    "start": window.start.isoformat(),
                    "end": window.end.isoformat(),
                    "step": str(step),
                    "timeout": "3s",
                },
            )
            if raw.get("status") != "success":
                raise BackendFailure("unavailable", "prometheus_query_failed")
            data = raw["data"]
            if data["resultType"] != "matrix" or not isinstance(data["result"], list):
                raise ValueError("invalid matrix")
            series = data["result"]
            if not series:
                return result("unavailable", limitations=["metric_samples_missing"])
            if len(series) != 1 or series[0]["metric"].get(self.label) != self.value:
                raise BackendFailure("error", "ambiguous_or_mismatched_series")
            values = series[0]["values"]
            if not isinstance(values, list) or len(values) > 301:
                raise ValueError("sample limit")
            points: list[JsonValue] = []
            positions: list[int] = []
            for timestamp, value in values:
                timestamp, value = float(timestamp), float(value)
                if not math.isfinite(timestamp) or not math.isfinite(value) or value < 0:
                    raise ValueError("invalid numeric sample")
                observed = datetime.fromtimestamp(timestamp, UTC)
                position = (timestamp - window.start.timestamp()) / step
                if (
                    not window.start <= observed <= window.end
                    or abs(position - round(position)) > 0.001
                ):
                    raise ValueError("sample outside window grid")
                positions.append(round(position))
                points.append({"at": observed.isoformat(), "value": value})
            if not points:
                return result("unavailable", limitations=["metric_samples_missing"])
            if positions != sorted(set(positions)):
                raise ValueError("duplicate or unordered samples")
            expected = math.floor((window.end - window.start).total_seconds() / step) + 1
            limitations = []
            if positions != list(range(expected)):
                limitations.append("metric_sample_gaps")
            if raw.get("warnings") or raw.get("infos"):
                limitations.append("backend_reported_query_limitations")
            return result(
                "ok",
                payload={
                    "metric_key": metric_key,
                    "step_seconds": step,
                    "unit": "connections"
                    if metric_key == "mysql.connections"
                    else "queries_per_second",
                    "samples": points,
                },
                limitations=limitations,
                observed_at=datetime.fromtimestamp(float(values[-1][0]), UTC),
            )
        except BackendFailure as exc:
            return result(exc.status, limitations=[exc.code])
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            IndexError,
            OverflowError,
            OSError,
        ) as exc:
            return result("error", limitations=["invalid_metric_response:" + type(exc).__name__])
