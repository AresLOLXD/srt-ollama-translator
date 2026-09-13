import pytest
import srt
from datetime import timedelta
from app.translator import SubtitleBlock, translate_block


def make_block(count: int) -> SubtitleBlock:
    subs = [
        srt.Subtitle(
            index=i + 1,
            start=timedelta(seconds=i),
            end=timedelta(seconds=i + 1),
            content=f"Line {i + 1}",
        )
        for i in range(count)
    ]
    return SubtitleBlock(subs=subs)


class FakeClient:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def chat(self, model: str, prompt: str) -> str:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.mark.asyncio
async def test_translate_block_succeeds_on_first_try():
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    block = make_block(2)
    result = await translate_block(client, "llama3.1", block, "en")
    assert result == {1: "Hola", 2: "Mundo"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_translate_block_retries_when_lines_missing():
    client = FakeClient(["[1] Hola", "[1] Hola\n[2] Mundo"])
    block = make_block(2)
    result = await translate_block(client, "llama3.1", block, "en")
    assert result == {1: "Hola", 2: "Mundo"}
    assert client.calls == 2


@pytest.mark.asyncio
async def test_translate_block_gives_up_after_max_retries():
    client = FakeClient(["[1] Hola"])
    block = make_block(2)
    result = await translate_block(client, "llama3.1", block, "en", max_retries=3)
    assert result == {1: "Hola"}
    assert client.calls == 3
