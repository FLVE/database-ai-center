import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import yaml

from dbic.adapters.agent.null import NullAgentRuntime
from dbic.adapters.metrics.null import NullMetrics
from dbic.adapters.registry import YamlRegistry
from dbic.adapters.search.null import NullSearch
from dbic.core.evidence import normalize
from dbic.core.models import DiagnosticTask, Evidence, TaskStatus, ToolResult, utc_now
from dbic.core.planner import PLAYBOOKS
from dbic.core.policy import TOOLS
from dbic.core.rules import evaluate
from dbic.core.runner import TaskRunner
from tests.fakes import FakeStore, FakeTools


def test_yaml_assets_match_executable_defaults():
    for playbook in PLAYBOOKS.values():
        assert (
            yaml.safe_load(Path(f"configs/playbooks/{playbook.id}.yaml").read_text())
            == playbook.model_dump()
        )
    policy = yaml.safe_load(Path("configs/policies.yaml").read_text())
    assert set(policy["allowed_tools"]) == TOOLS
    assert policy["allow_mutation"] is False
    assert policy["allowed_instances"] == ["mysql-lab-01"]
    assert policy["max_tool_calls"] == 8 and policy["max_duration_seconds"] == 90


def test_null_sources_are_unavailable_not_empty():
    async def check():
        metrics = await NullMetrics().query_range(
            instance_id="mysql-lab-01",
            metric_key="mysql.connections",
            start="2020-01-01T00:00:00Z",
            end="2020-01-02T00:00:00Z",
        )
        search = await NullSearch().search_events(
            instance_id="mysql-lab-01",
            start="2020-01-01T00:00:00Z",
            end="2020-01-02T00:00:00Z",
            categories=["error"],
        )
        assert metrics.status == search.status == "unavailable"
        assert metrics.limitations and search.limitations

    asyncio.run(check())


def test_stale_and_future_evidence_cannot_make_current_claim():
    for offset in (-100, 10):
        item = Evidence(
            task_id=uuid4(),
            source="mysql.lock_chain",
            kind="database_state",
            instance_id="mysql-lab-01",
            observed_at=utc_now() + timedelta(seconds=offset),
            status="empty",
            quality="observed",
            payload={"edges": []},
            redacted=True,
        )
        result = evaluate("lock_wait", [item])
        assert result.status == "inconclusive"
        assert not result.findings


def test_oversize_result_is_not_persisted():
    task = DiagnosticTask(
        requested_by="test",
        database_type="mysql",
        instance_id="mysql-lab-01",
        problem_type="lock_wait",
    )
    item = normalize(
        task,
        ToolResult(
            status="ok",
            source="mysql.lock_chain",
            observed_at=utc_now(),
            payload={"large": "x" * 65537},
            duration_ms=1,
        ),
    )
    assert item.status == "error"
    assert not item.payload


def test_runner_timeout_and_duplicate_claim():
    class SlowTools(FakeTools):
        async def run(self, **kwargs):
            await asyncio.sleep(1)

    async def check():
        registry = YamlRegistry(Path("configs/instances.yaml"))
        store = FakeStore()
        task = DiagnosticTask(
            requested_by="test",
            database_type="mysql",
            instance_id="mysql-lab-01",
            problem_type="lock_wait",
        )
        await store.create_task(task)
        runner = TaskRunner(registry, SlowTools(), store, NullAgentRuntime(), timeout=0.01)
        await asyncio.gather(runner.run(task.task_id), runner.run(task.task_id))
        assert store.tasks[task.task_id].status == TaskStatus.FAILED
        assert store.tasks[task.task_id].error_code == "task_timeout"
        assert not store.reports[task.task_id].findings

    asyncio.run(check())
