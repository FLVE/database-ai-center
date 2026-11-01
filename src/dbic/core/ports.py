from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from dbic.core.models import (
    DiagnosticStep,
    DiagnosticTask,
    Evidence,
    Feedback,
    Finding,
    Instance,
    Report,
    TaskDetail,
    TaskStatus,
    ToolResult,
)
from dbic.core.task_history import TaskListQuery, TaskPage


class AssetRegistryPort(Protocol):
    async def get_instance(self, instance_id: str) -> Instance | None: ...


class DiagnosticToolPort(Protocol):
    async def run(
        self, *, tool_id: str, instance_id: str, params: dict[str, JsonValue]
    ) -> ToolResult: ...


class MetricsPort(Protocol):
    async def query_range(
        self, *, instance_id: str, metric_key: str, start: str, end: str
    ) -> ToolResult: ...


class EvidenceSearchPort(Protocol):
    async def search_events(
        self, *, instance_id: str, start: str, end: str, categories: list[str]
    ) -> ToolResult: ...

    async def similar_incidents(self, *, query: str, limit: int) -> ToolResult: ...


class AgentRuntimePort(Protocol):
    async def explain(
        self, *, task: DiagnosticTask, evidence: list[Evidence], findings: list[Finding]
    ) -> Report: ...


class TaskStorePort(Protocol):
    async def register_instance(self, instance: Instance) -> None: ...
    async def create_task(self, task: DiagnosticTask) -> None: ...
    async def list_tasks(self, query: TaskListQuery) -> TaskPage: ...
    async def get_task(self, task_id: UUID) -> TaskDetail | None: ...
    async def get_evidence(self, task_id: UUID) -> list[Evidence]: ...
    async def update_task(self, task: DiagnosticTask, expected: TaskStatus) -> bool: ...
    async def save_step(self, step: DiagnosticStep, evidence: Evidence) -> None: ...
    async def finish_task(self, task: DiagnosticTask, report: Report) -> None: ...
    async def save_feedback(self, feedback: Feedback) -> None: ...
