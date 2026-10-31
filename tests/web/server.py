"""Browser fixture: real HTTP/API/engine, in-memory storage and database tools only."""

import asyncio
from pathlib import Path

from dbic.adapters.agent.null import NullAgentRuntime
from dbic.adapters.registry import YamlRegistry
from dbic.api.service import DiagnosticService
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
    runner = TaskRunner(registry, BrowserTools(), store, NullAgentRuntime())
    service = DiagnosticService(registry, store, runner)
    config = Settings(api_token=TOKEN, pg_password="browser-test-only")
    return create_app(config, readiness=lambda: True, diagnostics=service)
