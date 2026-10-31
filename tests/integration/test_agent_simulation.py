import asyncio
import os

import pytest

from dbic.adapters.agent.compatible import CompatibleAgentRuntime
from tests.unit.test_agent import config, investigation

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DBIC_LLM_SIMULATION_URL"), reason="local Postman simulation not running"
    ),
]


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("ok", "ok"),
        ("401", "unavailable"),
        ("429", "unavailable"),
        ("503", "unavailable"),
        ("timeout", "unavailable"),
        ("injection", "rejected"),
    ],
)
def test_real_adapter_against_postman_simulation(scenario, expected):
    adapter = CompatibleAgentRuntime(
        config(
            llm_base_url=os.environ["DBIC_LLM_SIMULATION_URL"],
            llm_model=scenario,
            llm_timeout_seconds=0.15 if scenario == "timeout" else 3,
        )
    )
    detail, items, tools = asyncio.run(investigation(adapter))
    assert detail.task.status == "completed"
    assert detail.report.agent_status == expected
    assert detail.report.findings
    assert "fixture-upstream-secret" not in detail.model_dump_json()
    assert tools.calls == ["mysql.health", "mysql.lock_chain"]
    if expected == "ok":
        assert detail.report.agent_suggestions[0].evidence_ids == [items[0].evidence_id]
    else:
        assert not detail.report.agent_suggestions
