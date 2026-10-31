from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dbic.core.engine import InvalidTransition, transition_task
from dbic.core.models import DiagnosticTask, Evidence, Finding, Report, TaskStatus, TimeWindow


def task():
    return DiagnosticTask(
        requested_by="local-user",
        database_type="mysql",
        instance_id="mysql-lab-01",
        problem_type="lock_wait",
    )


@pytest.mark.parametrize("source,target", list(product(TaskStatus, TaskStatus)))
def test_all_state_transitions(source, target):
    current = task()
    if source != TaskStatus.QUEUED:
        current = transition_task(current, TaskStatus.RUNNING)
    if source not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
        current = transition_task(current, source)
    allowed = (source == TaskStatus.QUEUED and target == TaskStatus.RUNNING) or (
        source == TaskStatus.RUNNING and target not in (TaskStatus.QUEUED, TaskStatus.RUNNING)
    )
    if allowed:
        updated = transition_task(current, target)
        assert updated.status == target
        assert updated.started_at is not None
        assert current.status == source
    else:
        with pytest.raises(InvalidTransition, match=str(current.task_id)):
            transition_task(current, target)


def test_utc_window_and_invalid_times():
    window = TimeWindow(start="2026-10-31T08:00:00+08:00", end="2026-10-31T09:00:00+08:00")
    assert window.start.hour == 0
    assert window.start.tzinfo == UTC
    for start, end in [
        ("2026-10-31T00:00:00", "2026-10-31T01:00:00Z"),
        ("2026-10-31T02:00:00Z", "2026-10-31T01:00:00Z"),
    ]:
        with pytest.raises(ValidationError):
            TimeWindow(start=start, end=end)
    current = task()
    with pytest.raises(ValidationError):
        transition_task(current, TaskStatus.RUNNING, at=current.created_at - timedelta(seconds=1))


def test_lifecycle_cannot_be_forged():
    values = task().model_dump()
    with pytest.raises(ValidationError):
        DiagnosticTask(**(values | {"status": "completed"}))
    with pytest.raises(ValidationError):
        DiagnosticTask(**(values | {"parent_task_id": values["task_id"]}))


def test_evidence_round_trip():
    evidence = Evidence(
        task_id=uuid4(),
        kind="database_state",
        source="mysql.health",
        instance_id="mysql-lab-01",
        observed_at=datetime.now(UTC),
        quality="observed",
        status="unavailable",
        limitations=["not implemented"],
    )
    assert Evidence.model_validate_json(evidence.model_dump_json()) == evidence
    assert evidence.redacted is False
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence.model_dump() | {"status": "success"})


def test_findings_and_report_references():
    evidence_id = uuid4()
    finding = Finding(statement="observation", certainty="observed", evidence_ids=[evidence_id])
    with pytest.raises(ValidationError):
        Finding(statement="unsupported", certainty="observed", evidence_ids=[])
    with pytest.raises(ValidationError):
        Finding(
            statement="root cause", certainty="confirmed_root_cause", evidence_ids=[evidence_id]
        )
    with pytest.raises(ValidationError):
        Report(task_id=uuid4(), status="observed", summary="result", findings=[finding])
    report = Report(
        task_id=uuid4(),
        status="observed",
        summary="result",
        findings=[finding],
        evidence_ids=[evidence_id],
    )
    assert Report.model_validate_json(report.model_dump_json()) == report
    assert Report(task_id=uuid4(), status="inconclusive", summary="missing evidence")


def test_core_has_no_infrastructure_imports():
    import ast
    import sys
    from pathlib import Path

    for path in Path("src/dbic/core").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                assert module.split(".")[0] in sys.stdlib_module_names | {"pydantic", "dbic"}, (
                    path,
                    module,
                )
                if module.startswith("dbic."):
                    assert module.startswith("dbic.core"), (path, module)
