from uuid import UUID

from dbic.core.models import DiagnosticTask, Feedback, TaskStatus, TimeWindow
from dbic.core.planner import plan
from dbic.core.policy import authorize
from dbic.core.ports import AssetRegistryPort, TaskStorePort
from dbic.core.runner import TaskRunner


class DiagnosticService:
    def __init__(
        self, registry: AssetRegistryPort, store: TaskStorePort, runner: TaskRunner
    ) -> None:
        self.registry, self.store, self.runner = registry, store, runner
        self.pending: set[UUID] = set()

    async def create(
        self,
        *,
        instance_id: str,
        problem_type: str,
        window: TimeWindow | None,
        parent_task_id: UUID | None,
        request_id: UUID,
    ) -> DiagnosticTask:
        instance = await self.registry.get_instance(instance_id)
        if instance is None:
            raise PermissionError("instance is not allowed")
        authorize(instance, "mysql.health")
        playbook = plan(problem_type)
        if parent_task_id:
            parent = await self.store.get_task(parent_task_id)
            if (
                parent is None
                or parent.task.instance_id != instance_id
                or parent.task.problem_type != problem_type
                or parent.task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
            ):
                raise ValueError("parent must be a terminal task for the same instance and problem")
        if len(self.pending) >= 16:
            raise OverflowError("local pending task limit reached")
        task = DiagnosticTask(
            instance_id=instance_id,
            database_type="mysql",
            problem_type=problem_type,
            requested_by="local-user",
            window=window,
            parent_task_id=parent_task_id,
            playbook_id=playbook.id,
            request_id=request_id,
        )
        self.pending.add(task.task_id)
        try:
            await self.store.register_instance(instance)
            await self.store.create_task(task)
        except BaseException:
            self.pending.discard(task.task_id)
            raise
        return task

    async def execute(self, task_id: UUID) -> None:
        try:
            await self.runner.run(task_id)
        finally:
            self.pending.discard(task_id)

    async def feedback(self, item: Feedback) -> None:
        detail = await self.store.get_task(item.task_id)
        if detail is None:
            raise LookupError("task not found")
        if detail.task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            raise ValueError("feedback requires a finished task")
        await self.store.save_feedback(item)
