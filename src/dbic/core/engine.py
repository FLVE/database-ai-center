from datetime import datetime

from dbic.core.models import DiagnosticTask, TaskStatus, utc_now


class InvalidTransition(ValueError):
    pass


def transition_task(
    task: DiagnosticTask,
    target: TaskStatus,
    *,
    at: datetime | None = None,
    error_code: str | None = None,
) -> DiagnosticTask:
    allowed = {
        TaskStatus.QUEUED: {TaskStatus.RUNNING},
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.PARTIAL,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
    }
    if target not in allowed.get(task.status, set()):
        raise InvalidTransition(f"task {task.task_id}: {task.status} -> {target} is forbidden")
    values = task.model_dump()
    values.update(status=target, error_code=error_code)
    values["started_at" if target == TaskStatus.RUNNING else "finished_at"] = at or utc_now()
    return DiagnosticTask.model_validate(values)
