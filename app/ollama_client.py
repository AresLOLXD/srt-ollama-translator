import asyncio

import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_backoff_seconds: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self.retry_backoff_seconds = retry_backoff_seconds

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]

    async def chat(self, model: str, prompt: str, max_retries: int = 3) -> str:
        last_error: httpx.TransportError | None = None
        for attempt in range(max_retries):
            try:
                async with self._client() as client:
                    response = await client.post(
                        f"{self.base_url}/api/chat",
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "stream": False,
                            "options": {"temperature": 0.2},
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data["message"]["content"]
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))
        raise last_error
