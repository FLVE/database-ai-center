import base64
import hashlib
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from dbic.core.models import DatabaseType, Model, TaskStatus, UtcTime


class TaskSummary(Model):
    task_id: UUID
    instance_id: str
    database_type: DatabaseType
    problem_type: str
    status: TaskStatus
    created_at: UtcTime
    finished_at: UtcTime | None = None


class TaskCursor(Model):
    version: Literal[1] = 1
    created_at: UtcTime
    task_id: UUID
    filter_key: str = Field(pattern=r"^[a-f0-9]{64}$")


def decode_cursor(value: str) -> TaskCursor:
    try:
        data = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        return TaskCursor.model_validate_json(data)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid task history cursor") from exc


class TaskListQuery(Model):
    instance_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: TaskStatus | None = None
    created_from: UtcTime | None = Field(default=None, description="Inclusive task creation time")
    created_until: UtcTime | None = Field(default=None, description="Exclusive task creation time")
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)

    def filter_key(self) -> str:
        data = self.model_dump_json(exclude={"cursor", "limit"})
        return hashlib.sha256(data.encode()).hexdigest()

    @property
    def position(self) -> TaskCursor | None:
        return decode_cursor(self.cursor) if self.cursor else None

    @model_validator(mode="after")
    def valid_page(self) -> Self:
        if self.created_from and self.created_until and self.created_until <= self.created_from:
            raise ValueError("created_until must be after created_from")
        if self.position and self.position.filter_key != self.filter_key():
            raise ValueError("cursor does not match task history filters")
        return self


class TaskPage(Model):
    items: list[TaskSummary]
    next_cursor: str | None = None


def task_page(query: TaskListQuery, rows: list[TaskSummary]) -> TaskPage:
    items = rows[: query.limit]
    cursor = None
    if len(rows) > query.limit:
        last = items[-1]
        position = TaskCursor(
            created_at=last.created_at, task_id=last.task_id, filter_key=query.filter_key()
        )
        cursor = base64.urlsafe_b64encode(position.model_dump_json().encode()).decode().rstrip("=")
    return TaskPage(items=items, next_cursor=cursor)
