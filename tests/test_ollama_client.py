import httpx
import pytest
from app.ollama_client import OllamaClient


@pytest.mark.asyncio
async def test_list_models_returns_model_names():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.1"}, {"name": "mistral"}]})

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    models = await client.list_models()
    assert models == ["llama3.1", "mistral"]


@pytest.mark.asyncio
async def test_chat_sends_model_and_prompt_and_returns_content():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"message": {"content": "hola mundo"}})

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    result = await client.chat("llama3.1", "translate: hello world")

    assert result == "hola mundo"
    assert b"llama3.1" in captured["body"]
    assert b"translate: hello world" in captured["body"]


@pytest.mark.asyncio
async def test_list_models_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_models()
