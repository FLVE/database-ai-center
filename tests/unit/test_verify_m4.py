import importlib
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("verify_m4")


@pytest.mark.parametrize(
    "source,status,required,passes",
    [
        ("redis.memory_breakdown", "ok", False, True),
        ("redis.memory_breakdown", "unavailable", False, False),
        ("redis.slowlog_summary", "empty", False, True),
        ("redis.slowlog_summary", "forbidden", False, False),
        ("redis.bigkey_audit_read", "unavailable", False, True),
        ("redis.bigkey_audit_read", "unavailable", True, False),
        ("redis.bigkey_audit_read", "error", False, False),
    ],
)
def test_verifier_rejects_real_source_failures(verifier, source, status, required, passes):
    task_id = str(uuid4())
    item = {
        "task_id": task_id,
        "instance_id": "redis-lab-01",
        "redacted": True,
        "source": source,
        "status": status,
        "payload": {"used_memory_bytes": 100},
        "limitations": ["source unavailable"],
    }
    if passes:
        verifier.verify_item(item, task_id, required)
    else:
        with pytest.raises(RuntimeError):
            verifier.verify_item(item, task_id, required)
    item["task_id"] = str(uuid4())
    with pytest.raises(RuntimeError, match="ownership"):
        verifier.verify_item(item, task_id, required)
