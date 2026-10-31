import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from dbic.adapters.agent.compatible import CompatibleAgentRuntime
from dbic.adapters.agent.deepseek import deepseek_schema
from dbic.core.models import AgentAdvice
from tests.unit.test_agent import completion, config, investigation, suggestion


def test_deepseek_schema_keeps_local_validation_and_required_fields():
    schema = deepseek_schema()
    encoded = json.dumps(schema)
    assert all(
        '"' + key + '"' not in encoded for key in ["$ref", "$defs", "const", "minItems", "maxItems"]
    )
    item = schema["properties"]["suggestions"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])
    assert item["properties"]["instance_id"]["enum"] == ["mysql-lab-01"]
    with pytest.raises(ValidationError):
        AgentAdvice.model_validate({"suggestions": [suggestion(str(uuid4()))] * 4})


@pytest.mark.parametrize("attack", [None, "foreign_id", "too_many", "extra_sql", "multiple_calls"])
def test_deepseek_request_and_output_guards(attack):
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/beta/chat/completions"
        assert body["max_tokens"] == 1024 and body["thinking"] == {"type": "disabled"}
        assert not {"max_completion_tokens", "store", "parallel_tool_calls"} & body.keys()
        assert body["tools"][0]["function"]["parameters"] == deepseek_schema()
        assert body["tools"][0]["function"]["strict"] is True
        context = json.loads(body["messages"][1]["content"])
        item = suggestion(context["evidence"][0]["evidence_id"])
        if attack == "foreign_id":
            item["evidence_ids"] = [str(uuid4())]
        if attack == "extra_sql":
            item["sql"] = "SELECT secret FROM users"
        raw = completion([item] * (4 if attack == "too_many" else 1))
        if attack == "multiple_calls":
            raw["choices"][0]["message"]["tool_calls"] *= 2
        return httpx.Response(200, json=raw)

    detail, _, tools = asyncio.run(
        investigation(
            CompatibleAgentRuntime(
                config(llm_provider="deepseek", llm_base_url="https://api.deepseek.com/beta"),
                transport=httpx.MockTransport(handler),
            )
        )
    )
    assert detail.task.status == "completed"
    assert detail.report.agent_status == ("ok" if attack is None else "rejected")
    assert len(calls) == 1 and len(tools.calls) == 2
