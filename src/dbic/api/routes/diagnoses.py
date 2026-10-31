import re
from typing import Literal, Self
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import Field, field_validator, model_validator

from dbic.api.service import DiagnosticService
from dbic.core.history import validate_window
from dbic.core.models import Evidence, Feedback, Model, TaskDetail, TaskStatus, TimeWindow
from dbic.core.planner import UnsupportedDiagnosis

router = APIRouter(prefix="/api/v1/diagnoses")


class CreateDiagnosis(Model):
    instance_id: str = Field(min_length=1, max_length=100)
    problem_type: Literal[
        "lock_wait",
        "slow_query",
        "memory_pressure",
        "activity_health",
        "maintenance_health",
        "cluster_health",
    ]
    window: TimeWindow | None = None
    parent_task_id: UUID | None = None

    @model_validator(mode="after")
    def historical_window(self) -> Self:
        if self.window:
            if self.problem_type not in {"lock_wait", "slow_query"}:
                raise ValueError("historical window unsupported for this problem")
            validate_window(self.window)
        return self


class Accepted(Model):
    task_id: UUID
    status: TaskStatus


class FeedbackBody(Model):
    verdict: Literal["correct", "partial", "incorrect"]
    confirmed_root_cause: str | None = Field(default=None, max_length=1000)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("confirmed_root_cause", "comment")
    @classmethod
    def redact(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = re.sub(r"\b\w+(?:\+\w+)?://\S+", "[REDACTED_URI]", value)
        return re.sub(
            r"(?i)\b(password|passwd|token|api_key|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", value
        )


def service(request: Request) -> DiagnosticService:
    return request.app.state.diagnostics  # type: ignore[no-any-return]


@router.post("", status_code=202, response_model=Accepted)
async def create(body: CreateDiagnosis, request: Request, background: BackgroundTasks) -> Accepted:
    runtime = service(request)
    try:
        task = await runtime.create(
            instance_id=body.instance_id,
            problem_type=body.problem_type,
            window=body.window,
            parent_task_id=body.parent_task_id,
            request_id=UUID(request.state.request_id),
        )
    except PermissionError as exc:
        raise HTTPException(403, "Instance is not allowed") from exc
    except UnsupportedDiagnosis as exc:
        raise HTTPException(422, "Unsupported database, problem or window") from exc
    except ValueError as exc:
        raise HTTPException(409, "Invalid retry parent") from exc
    except OverflowError as exc:
        raise HTTPException(429, "Local task capacity exceeded") from exc
    background.add_task(runtime.execute, task.task_id)
    return Accepted(task_id=task.task_id, status=task.status)


@router.get("/{task_id}", response_model=TaskDetail)
async def detail(task_id: UUID, request: Request) -> TaskDetail:
    result = await service(request).store.get_task(task_id)
    if result is None:
        raise HTTPException(404, "Task not found")
    return result


@router.get("/{task_id}/evidence", response_model=list[Evidence])
async def evidence(task_id: UUID, request: Request) -> list[Evidence]:
    await detail(task_id, request)
    return [item for item in await service(request).store.get_evidence(task_id) if item.redacted]


@router.post("/{task_id}/feedback", status_code=201, response_model=Feedback)
async def feedback(task_id: UUID, body: FeedbackBody, request: Request) -> Feedback:
    item = Feedback(task_id=task_id, **body.model_dump())
    try:
        await service(request).feedback(item)
    except LookupError as exc:
        raise HTTPException(404, "Task not found") from exc
    except ValueError as exc:
        raise HTTPException(409, "Task is not finished") from exc
    return item
