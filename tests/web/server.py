"""Browser fixture: real HTTP/API/engine, in-memory storage and database tools only."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from dbic.adapters.agent.null import NullAgentRuntime
from dbic.adapters.registry import YamlRegistry
from dbic.api.service import DiagnosticService
from dbic.core.models import DiagnosticTask, Report
from dbic.core.runner import TaskRunner
from dbic.main import create_app
from dbic.settings import Settings
from tests.fakes import FakeStore, FakeTools
from tests.unit.test_api import TOKEN


class BrowserTools(FakeTools):
    async def run(self, **kwargs):
        await asyncio.sleep(0.15)
        if kwargs["tool_id"] == "mysql.slow_query_summary":
            return await FakeTools("unavailable", {}, ["no timed application digest samples"]).run(
                **kwargs
            )
        return await super().run(**kwargs)


def app():
    registry = YamlRegistry(
        Path("configs/instances.yaml"),
        redis_enabled=True,
        mongodb_enabled=True,
        postgresql_enabled=True,
        doris_enabled=False,
    )
    store = FakeStore()
    for index in range(25):
        created = datetime(2024, 1, 1, 12, tzinfo=UTC) + timedelta(minutes=index)
        task = DiagnosticTask(
            task_id=UUID(int=1000 + index),
            requested_by="browser-history-fixture",
            instance_id="mysql-lab-01" if index < 20 else "redis-lab-01",
            database_type="mysql" if index < 20 else "redis",
            problem_type="lock_wait" if index < 20 else "memory_pressure",
            status=["completed", "partial", "failed"][index % 3],
            created_at=created,
            started_at=created,
            finished_at=created,
        )
        store.tasks[task.task_id] = task
        store.reports[task.task_id] = Report(
            task_id=task.task_id,
            status="inconclusive",
            summary="浏览器历史测试样例，无实际数据库采样。",
        )
    runner = TaskRunner(registry, BrowserTools(), store, NullAgentRuntime())
    service = DiagnosticService(registry, store, runner)
    config = Settings(api_token=TOKEN, pg_password="browser-test-only")
    return create_app(config, readiness=lambda: True, diagnostics=service)
