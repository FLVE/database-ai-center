from datetime import timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from dbic.core.models import Model, UtcTime

Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]


class MemorySnapshot(Model):
    used_memory_bytes: Count | None = None
    used_memory_rss_bytes: Count | None = None
    used_memory_dataset_bytes: Count | None = None
    used_memory_overhead_bytes: Count | None = None
    maxmemory_bytes: Count | None = None
    mem_not_counted_for_evict_bytes: Count | None = None
    mem_fragmentation_ratio: (
        Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None
    ) = None
    mem_fragmentation_bytes: (
        Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)] | None
    ) = None
    evicted_keys_total: Count | None = None


class CommandSummary(Model):
    command: Literal[
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
        "OTHER",
    ]
    count: Annotated[int, Field(strict=True, ge=1, le=20)]
    total_duration_us: Count


class SlowlogSummary(Model):
    sample_count: Annotated[int, Field(strict=True, ge=0, le=20)]
    earliest_at: UtcTime | None
    latest_at: UtcTime | None
    min_duration_us: Count | None
    max_duration_us: Count | None
    total_duration_us: Count
    commands: list[CommandSummary] = Field(max_length=20)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if sum(x.count for x in self.commands) != self.sample_count:
            raise ValueError("slowlog count mismatch")
        if sum(x.total_duration_us for x in self.commands) != self.total_duration_us:
            raise ValueError("slowlog duration mismatch")
        values = (self.earliest_at, self.latest_at, self.min_duration_us, self.max_duration_us)
        if self.sample_count == 0:
            if any(x is not None for x in values):
                raise ValueError("empty slowlog has samples")
        elif any(x is None for x in values):
            raise ValueError("slowlog sample metadata missing")
        elif (
            self.earliest_at > self.latest_at  # type: ignore[operator]
            or self.min_duration_us > self.max_duration_us  # type: ignore[operator]
            or self.max_duration_us > self.total_duration_us  # type: ignore[operator]
        ):
            raise ValueError("invalid slowlog bounds")
        return self


class AuditSnapshot(Model):
    schema_version: Literal[1]
    instance_id: Literal["redis-lab-01"]
    database_type: Literal["redis"]
    environment: Literal["lab"]
    audit_id: UUID
    started_at: UtcTime
    finished_at: UtcTime
    coverage: Literal["sampled", "full"]
    scanned_keys: Count
    bigkey_threshold_bytes: Annotated[int, Field(strict=True, gt=0)]
    bigkey_count: Count
    largest_key_bytes: Count

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not timedelta(0) <= self.finished_at - self.started_at <= timedelta(hours=1):
            raise ValueError("invalid audit interval")
        if self.bigkey_count > self.scanned_keys:
            raise ValueError("audit count exceeds sample")
        if self.scanned_keys == 0 and self.largest_key_bytes != 0:
            raise ValueError("empty audit has a size")
        if (self.bigkey_count > 0) != (self.largest_key_bytes >= self.bigkey_threshold_bytes):
            raise ValueError("audit threshold/count mismatch")
        return self


class AuditEvidence(AuditSnapshot):
    origin: Literal["imported_audit"]
