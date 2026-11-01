import asyncio
import threading
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, MetaData, Table, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dbic.core.models import (
    DiagnosticStep,
    DiagnosticTask,
    Evidence,
    Feedback,
    Instance,
    Report,
    TaskDetail,
    TaskStatus,
    utc_now,
)
from dbic.core.task_history import TaskListQuery, TaskPage, TaskSummary, task_page


def task_values(task: DiagnosticTask) -> dict[str, Any]:
    values = task.model_dump(exclude={"task_id", "window"})
    values.update(
        id=task.task_id,
        window_start=task.window.start if task.window else None,
        window_end=task.window.end if task.window else None,
    )
    return values


def task_model(row: Any) -> DiagnosticTask:
    values = dict(row)
    values["task_id"] = values.pop("id")
    start, end = values.pop("window_start"), values.pop("window_end")
    values["window"] = {"start": start, "end": end} if start else None
    return DiagnosticTask.model_validate(values)


def evidence_model(row: Any) -> Evidence:
    values = dict(row)
    values["evidence_id"] = values.pop("id")
    values["payload"] = values.pop("payload_json")
    values["limitations"] = values.pop("limitations_json")
    start, end = values.pop("window_start"), values.pop("window_end")
    values["window"] = {"start": start, "end": end} if start else None
    return Evidence.model_validate(values)


class PostgresTaskStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.metadata = MetaData()
        self._lock = threading.Lock()

    def table(self, name: str) -> Table:
        with self._lock:
            if not self.metadata.tables:
                self.metadata.reflect(
                    bind=self.engine,
                    only=[
                        "instances",
                        "diagnostic_tasks",
                        "diagnostic_steps",
                        "evidence",
                        "findings",
                        "reports",
                        "feedback",
                        "audit_events",
                    ],
                )
        return self.metadata.tables[name]

    async def register_instance(self, instance: Instance) -> None:
        def operation() -> None:
            table = self.table("instances")
            statement = pg_insert(table).values(**instance.model_dump())
            with self.engine.begin() as connection:
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[table.c.id], set_=instance.model_dump(exclude={"id"})
                    )
                )

        await asyncio.to_thread(operation)

    async def create_task(self, task: DiagnosticTask) -> None:
        def operation() -> None:
            table, audit = self.table("diagnostic_tasks"), self.table("audit_events")
            with self.engine.begin() as connection:
                connection.execute(insert(table).values(**task_values(task)))
                connection.execute(
                    insert(audit).values(
                        id=uuid4(),
                        task_id=task.task_id,
                        actor=task.requested_by,
                        action="task.created",
                        target=task.instance_id,
                        status="queued",
                        created_at=utc_now(),
                    )
                )

        await asyncio.to_thread(operation)

    async def get_task(self, task_id: UUID) -> TaskDetail | None:
        def operation() -> TaskDetail | None:
            tasks, steps, reports, feedback = [
                self.table(n)
                for n in ("diagnostic_tasks", "diagnostic_steps", "reports", "feedback")
            ]
            with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
                row = conn.execute(select(tasks).where(tasks.c.id == task_id)).mappings().first()
                if row is None:
                    return None
                report = conn.execute(
                    select(reports.c.report_json).where(reports.c.task_id == task_id)
                ).scalar()
                return TaskDetail(
                    task=task_model(row),
                    steps=[
                        DiagnosticStep.model_validate(dict(r))
                        for r in conn.execute(
                            select(steps)
                            .where(steps.c.task_id == task_id)
                            .order_by(steps.c.started_at)
                        ).mappings()
                    ],
                    report=Report.model_validate(report) if report else None,
                    feedback=[
                        Feedback.model_validate(dict(r))
                        for r in conn.execute(
                            select(feedback)
                            .where(feedback.c.task_id == task_id)
                            .order_by(feedback.c.created_at)
                        ).mappings()
                    ],
                )

        return await asyncio.to_thread(operation)

    async def list_tasks(self, query: TaskListQuery) -> TaskPage:
        def operation() -> TaskPage:
            table = self.table("diagnostic_tasks")
            statement = select(
                table.c.id.label("task_id"),
                table.c.instance_id,
                table.c.database_type,
                table.c.problem_type,
                table.c.status,
                table.c.created_at,
                table.c.finished_at,
            )
            if query.instance_id is not None:
                statement = statement.where(table.c.instance_id == query.instance_id)
            if query.status is not None:
                statement = statement.where(table.c.status == query.status.value)
            if query.created_from is not None:
                statement = statement.where(table.c.created_at >= query.created_from)
            if query.created_until is not None:
                statement = statement.where(table.c.created_at < query.created_until)
            if position := query.position:
                statement = statement.where(
                    tuple_(table.c.created_at, table.c.id)
                    < tuple_(position.created_at, position.task_id)
                )
            statement = statement.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(
                query.limit + 1
            )
            with self.engine.connect() as conn:
                rows = [
                    TaskSummary.model_validate(dict(r)) for r in conn.execute(statement).mappings()
                ]
            return task_page(query, rows)

        return await asyncio.to_thread(operation)

    async def get_evidence(self, task_id: UUID) -> list[Evidence]:
        def operation() -> list[Evidence]:
            table = self.table("evidence")
            with self.engine.connect() as conn:
                return [
                    evidence_model(row)
                    for row in conn.execute(
                        select(table)
                        .where(table.c.task_id == task_id)
                        .order_by(table.c.collected_at)
                    ).mappings()
                ]

        return await asyncio.to_thread(operation)

    async def update_task(self, task: DiagnosticTask, expected: TaskStatus) -> bool:
        def operation() -> bool:
            table = self.table("diagnostic_tasks")
            with self.engine.begin() as conn:
                result = conn.execute(
                    update(table)
                    .where(table.c.id == task.task_id, table.c.status == expected)
                    .values(**task_values(task))
                )
                return result.rowcount == 1

        return await asyncio.to_thread(operation)

    async def save_step(self, step: DiagnosticStep, evidence: Evidence) -> None:
        if (
            step.task_id != evidence.task_id
            or step.tool_id != evidence.source
            or not evidence.redacted
        ):
            raise ValueError("step/evidence identity or redaction mismatch")

        def operation() -> None:
            tasks, steps, evidences, audit = [
                self.table(n)
                for n in ("diagnostic_tasks", "diagnostic_steps", "evidence", "audit_events")
            ]
            with self.engine.begin() as conn:
                task = (
                    conn.execute(select(tasks).where(tasks.c.id == step.task_id).with_for_update())
                    .mappings()
                    .one()
                )
                if task["status"] != "running" or task["instance_id"] != evidence.instance_id:
                    raise ValueError("evidence does not belong to a running task instance")
                values = evidence.model_dump(
                    exclude={"evidence_id", "payload", "limitations", "window"}
                )
                values.update(
                    id=evidence.evidence_id,
                    payload_json=evidence.payload,
                    limitations_json=evidence.limitations,
                    window_start=evidence.window.start if evidence.window else None,
                    window_end=evidence.window.end if evidence.window else None,
                )
                conn.execute(insert(evidences).values(**values))
                conn.execute(insert(steps).values(**step.model_dump()))
                conn.execute(
                    insert(audit).values(
                        id=uuid4(),
                        task_id=step.task_id,
                        actor=task["requested_by"],
                        action="tool.executed",
                        target=step.tool_id,
                        status=step.status,
                        elapsed_ms=step.elapsed_ms,
                        created_at=utc_now(),
                    )
                )

        await asyncio.to_thread(operation)

    async def finish_task(self, task: DiagnosticTask, report: Report) -> None:
        if task.task_id != report.task_id or task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            raise ValueError("report requires matching terminal task")

        def operation() -> None:
            tasks, evidences, reports, findings, audit = [
                self.table(n)
                for n in ("diagnostic_tasks", "evidence", "reports", "findings", "audit_events")
            ]
            with self.engine.begin() as conn:
                current = (
                    conn.execute(select(tasks).where(tasks.c.id == task.task_id).with_for_update())
                    .mappings()
                    .one()
                )
                if current["status"] != "running":
                    raise ValueError("terminal transition requires a running task")
                actual = set(
                    conn.execute(
                        select(evidences.c.id).where(evidences.c.task_id == task.task_id)
                    ).scalars()
                )
                if not set(report.evidence_ids) <= actual:
                    raise ValueError("report references evidence outside its persisted task")
                conn.execute(
                    insert(reports).values(
                        task_id=task.task_id,
                        summary_md=report.summary,
                        report_json=report.model_dump(mode="json"),
                        generated_at=report.generated_at,
                        generator_version=report.generator_version,
                    )
                )
                for finding in report.findings:
                    conn.execute(
                        insert(findings).values(
                            id=finding.finding_id,
                            task_id=task.task_id,
                            statement=finding.statement,
                            certainty=finding.certainty,
                            rule_id=finding.rule_id,
                            confirmed_by=finding.confirmed_by,
                            evidence_ids_json=[str(i) for i in finding.evidence_ids],
                        )
                    )
                conn.execute(
                    update(tasks).where(tasks.c.id == task.task_id).values(**task_values(task))
                )
                conn.execute(
                    insert(audit).values(
                        id=uuid4(),
                        task_id=task.task_id,
                        actor=task.requested_by,
                        action="task.finished",
                        target=task.instance_id,
                        status=task.status,
                        created_at=utc_now(),
                    )
                )

        await asyncio.to_thread(operation)

    async def save_feedback(self, feedback: Feedback) -> None:
        def operation() -> None:
            table, tasks, audit = [
                self.table(n) for n in ("feedback", "diagnostic_tasks", "audit_events")
            ]
            with self.engine.begin() as conn:
                task = (
                    conn.execute(select(tasks).where(tasks.c.id == feedback.task_id))
                    .mappings()
                    .one()
                )
                if task["status"] in {"queued", "running"}:
                    raise ValueError("feedback requires a finished task")
                conn.execute(insert(table).values(**feedback.model_dump()))
                conn.execute(
                    insert(audit).values(
                        id=uuid4(),
                        task_id=feedback.task_id,
                        actor="local-user",
                        action="feedback.created",
                        target=str(feedback.id),
                        status=feedback.verdict,
                        created_at=utc_now(),
                    )
                )

        await asyncio.to_thread(operation)
