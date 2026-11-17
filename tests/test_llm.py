"""The Ollama transport maps our request onto Ollama's chat API (no server needed)."""

import io
import json

from signaldesk import llm

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
REQUEST = {
    "model": "qwen2.5:7b", "max_tokens": 500, "system": "Use only the passages.",
    "messages": [{"role": "user", "content": "Question?"}],
    "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
}


def _fake_urlopen(reply: dict, seen: list):
    def urlopen(req, timeout):
        seen.append(json.loads(req.data))
        return io.BytesIO(json.dumps(reply).encode())
    return urlopen


def test_ollama_request_and_response_mapping(monkeypatch):
    seen: list = []
    reply = {"message": {"content": '{"answer": "x"}'}, "done_reason": "stop",
             "prompt_eval_count": 120, "eval_count": 8}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(reply, seen))
    assert llm.ollama_transport(REQUEST) == ('{"answer": "x"}', "end_turn", 120, 8)
    body = seen[0]
    assert body["format"] == SCHEMA and body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "Use only the passages."}
    assert body["options"]["num_ctx"] >= 8192 and body["options"]["temperature"] == 0


def test_ollama_truncation_is_reported_as_max_tokens(monkeypatch):
    reply = {"message": {"content": '{"ans'}, "done_reason": "length"}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(reply, []))
    assert llm.ollama_transport(REQUEST)[1] == "max_tokens"


def test_ollama_down_says_how_to_start_it(monkeypatch):
    import urllib.error

    import pytest

    def refuse(req, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(RuntimeError, match="ollama serve"):
        llm.ollama_transport(REQUEST)
