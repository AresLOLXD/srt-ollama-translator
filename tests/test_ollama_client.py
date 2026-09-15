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


@pytest.mark.asyncio
async def test_chat_retries_on_transport_error_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"message": {"content": "hola"}})

    client = OllamaClient(
        "http://fake-ollama:11434",
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    result = await client.chat("llama3.1", "hello")

    assert result == "hola"
    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_chat_raises_after_exhausting_retries():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    client = OllamaClient(
        "http://fake-ollama:11434",
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    with pytest.raises(httpx.RemoteProtocolError):
        await client.chat("llama3.1", "hello")

    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_chat_does_not_retry_http_status_errors():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500)

    client = OllamaClient(
        "http://fake-ollama:11434",
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat("llama3.1", "hello")

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_chat_sends_low_temperature_option():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"message": {"content": "hola"}})

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    await client.chat("llama3.1", "hello")

    assert b'"temperature": 0.2' in captured["body"] or b'"temperature":0.2' in captured["body"]
