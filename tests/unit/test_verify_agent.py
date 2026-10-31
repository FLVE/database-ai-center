import asyncio
import importlib
from pathlib import Path
from uuid import uuid4

import pytest

from dbic.adapters.agent.null import NullAgentRuntime
from tests.unit.test_agent import investigation, suggestion


@pytest.mark.parametrize("scenario", ["valid", "fallback", "foreign_id", "extra_tool"])
def test_live_verifier_rejects_fallback_and_invalid_evidence(monkeypatch, scenario):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    verifier = importlib.import_module("verify_agent")
    detail, evidence, _ = asyncio.run(investigation(NullAgentRuntime()))
    detail = detail.model_dump(mode="json")
    evidence = [item.model_dump(mode="json") for item in evidence]
    detail["report"]["agent_status"] = "ok"
    detail["report"]["agent_suggestions"] = [suggestion(evidence[0]["evidence_id"])]
    if scenario == "fallback":
        detail["report"]["agent_status"] = "unavailable"
    elif scenario == "foreign_id":
        detail["report"]["agent_suggestions"][0]["evidence_ids"] = [str(uuid4())]
    elif scenario == "extra_tool":
        detail["steps"].append(detail["steps"][0])
    if scenario == "valid":
        assert verifier.check_report(detail, evidence) == 1
    else:
        with pytest.raises(RuntimeError):
            verifier.check_report(detail, evidence)
