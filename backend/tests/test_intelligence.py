import json

import httpx
import pytest
import respx

from zenkai.config import Settings
from zenkai.intelligence import diagnose


@respx.mock
async def test_openrouter_uses_strict_schema_and_actual_evidence():
    config = Settings(_env_file=None, openrouter_api_key="fixture-key")
    proposal = {"root_cause": "The gateway points to an unregistered upstream port", "evidence_ids": ["log-0"],
                "action": "restore_upstream", "target": "gateway", "upstream_port": 8000,
                "schema_field": None, "capacity": None, "verification_plan": "Check correct responses under continuing traffic"}
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(proposal)}}]}))
    result = await diagnose(config, [{"id": "log-0", "source": "loki", "data": "connection refused"}])
    payload = json.loads(route.calls[0].request.content)
    assert result.upstream_port == 8000
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["provider"]["require_parameters"] is True
    assert "connection refused" in payload["messages"][1]["content"]


async def test_model_diagnosis_requires_loki_evidence():
    with pytest.raises(ValueError, match="No Loki"):
        await diagnose(Settings(_env_file=None, openrouter_api_key="fixture-key"), [{"source": "health_probe"}])
