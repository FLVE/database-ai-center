import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.engine import make_url

from dbic.adapters.persistence.postgres import database_ready
from dbic.adapters.persistence.tasks import PostgresTaskStore
from dbic.core.models import DiagnosticTask, Instance, TaskStatus
from dbic.core.task_history import TaskListQuery

pytestmark = pytest.mark.integration


def test_real_postgres_history_filters_pages_and_indexes(monkeypatch):
    url = os.environ.get("DBIC_TEST_DATABASE_URL")
    if not url:
        pytest.skip("DBIC_TEST_DATABASE_URL not set; dedicated PostgreSQL required")
    assert make_url(url).database == "dbic_test", "history fixtures require dbic_test"
    monkeypatch.setenv("DBIC_DATABASE_URL", url)
    monkeypatch.setenv("DBIC_API_TOKEN", "integration-test-token-not-real")
    for _ in range(2):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    engine = create_engine(url)
    store = PostgresTaskStore(engine)
    instance_ids = ["history-" + uuid4().hex, "history-" + uuid4().hex]
    ids = []
    base = datetime.now(UTC).replace(year=2099)

    async def check():
        for name in instance_ids:
            await store.register_instance(
                Instance(
                    id=name,
                    type="mysql",
                    version="8.4",
                    connection_ref="env:HISTORY_FIXTURE",
                    enabled=False,
                )
            )
        for i in range(23):
            status = list(TaskStatus)[i % 6]
            created = base + timedelta(seconds=i // 3)
            task = DiagnosticTask(
                instance_id=instance_ids[i % 2],
                database_type="mysql",
                requested_by="history-test",
                problem_type="tool:mysql.health" if i == 0 else "lock_wait",
                status=status,
                created_at=created,
                started_at=None if status == TaskStatus.QUEUED else created,
                finished_at=None if status in {TaskStatus.QUEUED, TaskStatus.RUNNING} else created,
            )
            ids.append(task.task_id)
            await store.create_task(task)
        query = TaskListQuery(created_from=base, created_until=base + timedelta(seconds=8), limit=4)
        seen = []
        first = await store.list_tasks(query)
        page = first
        while True:
            assert len(page.items) <= 4
            seen.extend(page.items)
            if page.next_cursor is None:
                break
            page = await store.list_tasks(
                TaskListQuery(**{**query.model_dump(), "cursor": page.next_cursor})
            )
        assert len(seen) == len(set(t.task_id for t in seen)) == 23
        assert {t.task_id for t in seen} == set(ids)
        assert [(t.created_at, t.task_id) for t in seen] == sorted(
            [(t.created_at, t.task_id) for t in seen], reverse=True
        )
        newest = DiagnosticTask(
            instance_id=instance_ids[0],
            database_type="mysql",
            requested_by="history-test",
            problem_type="lock_wait",
            created_at=base + timedelta(seconds=7, microseconds=1),
        )
        ids.append(newest.task_id)
        await store.create_task(newest)
        second = await store.list_tasks(
            TaskListQuery(**{**query.model_dump(), "cursor": first.next_cursor})
        )
        assert [t.task_id for t in second.items] == [t.task_id for t in seen[4:8]]
        for status in TaskStatus:
            page = await store.list_tasks(
                TaskListQuery(
                    instance_id=instance_ids[0],
                    status=status,
                    created_from=base,
                    created_until=base + timedelta(seconds=3),
                    limit=100,
                )
            )
            assert {t.task_id for t in page.items} == {
                ids[i] for i in range(9) if i % 2 == 0 and list(TaskStatus)[i % 6] == status
            }
            assert page.next_cursor is None
        empty = await store.list_tasks(TaskListQuery(instance_id="' OR 1=1 --"))
        assert not empty.items and empty.next_cursor is None
        assert (await store.get_task(ids[0])).task.problem_type == "tool:mysql.health"

    try:
        assert database_ready(engine)
        indexes = {
            i["name"]: i["column_names"] for i in inspect(engine).get_indexes("diagnostic_tasks")
        }
        assert indexes["ix_tasks_created_id"] == ["created_at", "id"]
        assert indexes["ix_tasks_status_created_id"] == ["status", "created_at", "id"]
        asyncio.run(check())
        with engine.connect() as conn:
            events = store.table("audit_events")
            actions = (
                conn.execute(select(events.c.action).where(events.c.task_id.in_(ids)))
                .scalars()
                .all()
            )
            assert actions == ["task.created"] * len(ids)
        subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0002_m1"], check=True)
        assert not database_ready(engine)
        assert "ix_tasks_created_id" not in {
            i["name"] for i in inspect(engine).get_indexes("diagnostic_tasks")
        }
        with engine.connect() as conn:
            table = store.table("diagnostic_tasks")
            assert set(
                conn.execute(select(table.c.id).where(table.c.id.in_(ids))).scalars()
            ) == set(ids)
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
        assert database_ready(engine)
    finally:
        with engine.begin() as conn:
            for name in ["audit_events", "diagnostic_tasks"]:
                table = store.table(name)
                column = table.c.id if name == "diagnostic_tasks" else table.c.task_id
                conn.execute(delete(table).where(column.in_(ids)))
            table = store.table("instances")
            conn.execute(delete(table).where(table.c.id.in_(instance_ids)))
        engine.dispose()
