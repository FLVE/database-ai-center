from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from dbic.core.models import Model

Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]


class BackendHealth(Model):
    online_backends: Count
    total_backends: Count

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.online_backends > self.total_backends:
            raise ValueError("online backends exceed total")
        return self


class ProfileSample(Model):
    duration_ms: Annotated[float, Field(strict=True, ge=0, le=86400000)]
    state: Literal["OK", "EOF", "FINISHED", "CANCELLED", "ERROR", "RUNNING", "OTHER"]


class ProfileSummary(Model):
    samples: list[ProfileSample] = Field(max_length=20)
    truncated: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.truncated and len(self.samples) != 20:
            raise ValueError("truncation requires a full sample")
        return self
