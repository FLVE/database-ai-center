from datetime import timedelta

from dbic.core.models import TimeWindow, utc_now

METRIC_SOURCES = {
    "mysql.connections": "metrics.mysql_connections",
    "mysql.slow_queries": "metrics.mysql_slow_queries",
}
EVENT_SOURCE = "search.mysql_events"
CATEGORIES = frozenset({"error", "slow_query", "deadlock"})


def validate_window(window: TimeWindow) -> None:
    if window.end - window.start > timedelta(hours=24):
        raise ValueError("historical window cannot exceed 24 hours")
    if window.end > utc_now() + timedelta(seconds=5):
        raise ValueError("historical window cannot be in the future")


def request_window(instance_id: str, start: str, end: str) -> TimeWindow:
    if instance_id != "mysql-lab-01":
        raise ValueError("historical instance is not allowed")
    window = TimeWindow.model_validate({"start": start, "end": end})
    validate_window(window)
    return window
