from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from dbic.core.models import Model

Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
MemberState = Literal[
    "STARTUP",
    "PRIMARY",
    "SECONDARY",
    "RECOVERING",
    "STARTUP2",
    "UNKNOWN",
    "ARBITER",
    "DOWN",
    "ROLLBACK",
    "REMOVED",
]


class OperationSample(Model):
    operation: Literal["query", "getmore", "insert", "update", "remove", "command", "OTHER"]
    running_seconds: Count | None
    waiting_for_lock: Annotated[bool, Field(strict=True)] | None


class CurrentOperations(Model):
    samples: list[OperationSample] = Field(max_length=50)
    truncated: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.truncated and len(self.samples) != 50:
            raise ValueError("truncation requires a full sample")
        return self


class ReplicaMember(Model):
    member_id: Count
    state: MemberState
    reachable: Annotated[bool, Field(strict=True)]
    is_self: Literal[True]


class ReplicaSnapshot(Model):
    local_state: MemberState
    members: list[ReplicaMember] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.members[0].state != self.local_state:
            raise ValueError("local/member state mismatch")
        return self
