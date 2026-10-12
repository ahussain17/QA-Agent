from __future__ import annotations

import httpx

from p2p_qa.agents import AdaptiveRequester, _resource_id
from p2p_qa.client import ApiClient
from p2p_qa.payloads import PayloadFactory
from p2p_qa.schema import Operation, SchemaExplorer
from p2p_qa.utils import sanitize, validate_base_url


class RepairReasoner:
    model_name = "test"

    def __init__(self, values):
        self.values = iter(values)

    def plan(self, discovery):
        return []

    def repair(self, operation, payload, error):
        return next(self.values, None)

    def explain(self, title, evidence):
        return "test"


def test_unexpected_schema_recovery():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(422, json={"detail": "renamed field"})
        return httpx.Response(201, json={"id": "ok"})

    schema = {"type": "object", "properties": {"new_name": {"type": "string"}}, "required": ["new_name"]}
    op = Operation("POST", "/thing", "create_thing", None, schema, {})
    with ApiClient("http://localhost:8000", transport=httpx.MockTransport(handler)) as client:
        explorer = SchemaExplorer(client)
        requester = AdaptiveRequester(client, explorer, RepairReasoner([{"new_name": "fixed"}]), PayloadFactory())
        result, steps, payload = requester.request(op, "/thing", {"new_name": "bad"}, {}, name="x", purpose="x", expected="x")
    assert result.status_code == 201
    assert len(steps) == 2
    assert payload == {"new_name": "fixed"}


def test_recovery_limit_is_two_retries():
    def handler(request):
        return httpx.Response(422, json={"detail": "still wrong"})

    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    op = Operation("POST", "/thing", "create_thing", None, schema, {})
    with ApiClient("http://localhost:8000", transport=httpx.MockTransport(handler)) as client:
        requester = AdaptiveRequester(client, SchemaExplorer(client), RepairReasoner([{"value": "b"}, {"value": "c"}]), PayloadFactory())
        result, steps, _ = requester.request(op, "/thing", {"value": "a"}, {}, name="x", purpose="x", expected="x")
    assert result.status_code == 422
    assert len(steps) == 3


def test_secret_redaction_and_url_safety():
    redacted = sanitize({"Authorization": "Bearer abc.def", "nested": {"api_key": "sk-supersecret123"}, "text": "token sk-abcdefghijk"})
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert "sk-" not in redacted["text"]
    try:
        validate_base_url("https://example.com")
    except ValueError as exc:
        assert "--allow-nonlocal" in str(exc)
    else:
        raise AssertionError("nonlocal URL was accepted")


def test_client_rejects_absolute_escape():
    with ApiClient("http://localhost:8000", transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        try:
            client.request("GET", "https://evil.example/")
        except ValueError:
            pass
        else:
            raise AssertionError("absolute URL was accepted")


def test_line_id_is_taken_from_nested_po_line_not_top_level_po():
    response = {"id": "po-1", "lines": [{"id": "line-1", "sku": "STONE-1"}]}
    assert _resource_id(response, "line") == "line-1"

