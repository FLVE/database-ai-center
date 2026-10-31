from dbic.core.models import TaskDetail, ToolResult, utc_now


class FakeStore:
    def __init__(self):
        self.tasks = {}
        self.evidence = {}
        self.steps = {}
        self.reports = {}
        self.feedback = {}

    async def register_instance(self, instance):
        self.instance = instance

    async def create_task(self, task):
        self.tasks[task.task_id] = task

    async def get_task(self, task_id):
        if task_id not in self.tasks:
            return None
        return TaskDetail(
            task=self.tasks[task_id],
            steps=self.steps.get(task_id, []),
            report=self.reports.get(task_id),
            feedback=self.feedback.get(task_id, []),
        )

    async def get_evidence(self, task_id):
        return self.evidence.get(task_id, [])

    async def update_task(self, task, expected):
        if self.tasks[task.task_id].status != expected:
            return False
        self.tasks[task.task_id] = task
        return True

    async def save_step(self, step, evidence):
        self.steps.setdefault(step.task_id, []).append(step)
        self.evidence.setdefault(step.task_id, []).append(evidence)

    async def finish_task(self, task, report):
        actual = {item.evidence_id for item in self.evidence.get(task.task_id, [])}
        assert set(report.evidence_ids) <= actual
        self.tasks[task.task_id] = task
        self.reports[task.task_id] = report

    async def save_feedback(self, feedback):
        self.feedback.setdefault(feedback.task_id, []).append(feedback)


class FakeTools:
    def __init__(self, status="ok", payload=None, limitations=None):
        self.status = status
        self.payload = payload if payload is not None else {"edges": []}
        self.limitations = limitations or []
        self.calls = []

    async def run(self, *, tool_id, instance_id, params):
        self.calls.append(tool_id)
        if tool_id == "mysql.health":
            return ToolResult(
                status="ok",
                source=tool_id,
                observed_at=utc_now(),
                payload={"version": "8.4.0"},
                duration_ms=1,
            )
        return ToolResult(
            status=self.status,
            source=tool_id,
            observed_at=utc_now(),
            payload=self.payload,
            duration_ms=2,
            limitations=self.limitations,
        )
