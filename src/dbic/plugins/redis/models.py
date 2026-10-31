import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from dbic.core.redis_models import CommandSummary, MemorySnapshot, SlowlogSummary

MEMORY_FIELDS = {
    "used_memory": "used_memory_bytes",
    "used_memory_rss": "used_memory_rss_bytes",
    "used_memory_dataset": "used_memory_dataset_bytes",
    "used_memory_overhead": "used_memory_overhead_bytes",
    "maxmemory": "maxmemory_bytes",
    "mem_not_counted_for_evict": "mem_not_counted_for_evict_bytes",
    "mem_fragmentation_ratio": "mem_fragmentation_ratio",
    "mem_fragmentation_bytes": "mem_fragmentation_bytes",
    "evicted_keys": "evicted_keys_total",
}
COMMANDS = frozenset(
    {
        "GET",
        "SET",
        "MGET",
        "MSET",
        "HGET",
        "HSET",
        "LPUSH",
        "LRANGE",
        "SADD",
        "ZADD",
        "PING",
        "INFO",
    }
)


def memory_snapshot(memory: dict[str, Any], stats: dict[str, Any]) -> MemorySnapshot:
    values = memory | {"evicted_keys": stats.get("evicted_keys")}
    used = values.get("used_memory")
    if type(used) is int and used >= 0:
        # Startup accounting can underflow dataset bytes; preserve the gap, never invent zero.
        for key in ("used_memory_dataset", "used_memory_overhead"):
            value = values.get(key)
            if type(value) is int and (value < 0 or value > used):
                values[key] = None
    try:
        return MemorySnapshot.model_validate(
            {out: values.get(key) for key, out in MEMORY_FIELDS.items()}
        )
    except ValidationError as exc:
        fields = [
            error["loc"][0]
            for error in exc.errors(include_input=False)
            if error["loc"] and error["loc"][0] in MEMORY_FIELDS.values()
        ]
        logging.getLogger("dbic.redis").warning("invalid Redis memory fields=%s", fields)
        raise ValueError("invalid memory snapshot") from exc


def slowlog_summary(rows: Any, now: datetime) -> SlowlogSummary:
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("invalid slowlog response")
    groups: dict[str, tuple[int, int]] = {}
    times, durations = [], []
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("unsupported slowlog entry")
        _, timestamp, duration, args, _, _ = row
        if (
            type(timestamp) is not int
            or timestamp < 0
            or type(duration) is not int
            or duration < 0
            or not isinstance(args, list)
            or not args
            or not isinstance(args[0], bytes)
        ):
            raise ValueError("invalid slowlog sample")
        stamp = datetime.fromtimestamp(timestamp, UTC)
        if stamp > now + timedelta(seconds=5):
            raise ValueError("future slowlog sample")
        name = args[0].decode("ascii", errors="replace").upper() if len(args[0]) <= 16 else "OTHER"
        name = name if name in COMMANDS else "OTHER"
        count, total = groups.get(name, (0, 0))
        groups[name] = count + 1, total + duration
        times.append(stamp)
        durations.append(duration)
    return SlowlogSummary(
        sample_count=len(rows),
        earliest_at=min(times) if times else None,
        latest_at=max(times) if times else None,
        min_duration_us=min(durations) if durations else None,
        max_duration_us=max(durations) if durations else None,
        total_duration_us=sum(durations),
        commands=[
            CommandSummary(command=name, count=count, total_duration_us=total)
            for name, (count, total) in sorted(groups.items())
        ],
    )
