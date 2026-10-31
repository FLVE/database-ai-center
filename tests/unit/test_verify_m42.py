import importlib
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("verify_m42")


@pytest.mark.parametrize("status", ["forbidden", "unavailable", "error", "timeout"])
@pytest.mark.parametrize("source", ["mongodb.current_ops_summary", "mongodb.replica_health"])
def test_verifier_never_accepts_failed_sources(verifier, status, source):
    item = {
        "task_id": "task",
        "instance_id": "mongodb-lab-01",
        "redacted": True,
        "source": source,
        "status": status,
        "payload": {},
    }
    with pytest.raises(RuntimeError):
        verifier.verify_item(item, "task")


def test_empty_is_not_an_error_and_wrong_owner_is_rejected(verifier):
    item = {
        "task_id": "task",
        "instance_id": "mongodb-lab-01",
        "redacted": True,
        "source": "mongodb.current_ops_summary",
        "status": "empty",
        "payload": {"samples": [], "truncated": False},
    }
    verifier.verify_item(item, "task")
    with pytest.raises(RuntimeError, match="ownership"):
        verifier.verify_item(item, "other")
    item["status"] = "ok"
    with pytest.raises(RuntimeError, match="inconsistent"):
        verifier.verify_item(item, "task")
