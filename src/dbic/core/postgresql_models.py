from typing import Annotated

from pydantic import Field

from dbic.core.models import Model, UtcTime

Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Pid = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
BlockerPid = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]


class BlockingSession(Model):
    waiter_pid: Pid
    blocker_pids: list[BlockerPid] = Field(min_length=1, max_length=50)
    transaction_age_seconds: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None


class BlockingSnapshot(Model):
    sessions: list[BlockingSession] = Field(max_length=50)
    truncated: Annotated[bool, Field(strict=True)]


class VacuumTable(Model):
    relation_id: Annotated[int, Field(strict=True, gt=0)]
    live_tuples_estimate: Count
    dead_tuples_estimate: Count
    last_vacuum_at: UtcTime | None
    last_autovacuum_at: UtcTime | None
    vacuum_count: Count
    autovacuum_count: Count


class VacuumSnapshot(Model):
    tables: list[VacuumTable] = Field(max_length=50)
    truncated: Annotated[bool, Field(strict=True)]
    stats_reset_at: UtcTime | None
