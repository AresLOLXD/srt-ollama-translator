import pytest
import srt
from datetime import timedelta
from app.translator import SubtitleBlock, read_srt_text, translate_block, translate_srt_file


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


@pytest.mark.asyncio
async def test_translate_srt_file_writes_translated_output(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    progress_calls = []

    total_blocks, failed_blocks = await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        on_block_translated=lambda done, total: progress_calls.append((done, total)),
        block_size=25,
    )

    assert total_blocks == 1
    assert failed_blocks == 0
    assert progress_calls == [(1, 1)]
    output_text = output_path.read_text(encoding="utf-8")
    assert "Hola" in output_text
    assert "Mundo" in output_text


@pytest.mark.asyncio
async def test_translate_srt_file_keeps_original_text_for_failed_lines(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola"])

    total_blocks, failed_blocks = await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path)
    )

    assert total_blocks == 1
    assert failed_blocks == 1
    output_text = output_path.read_text(encoding="utf-8")
    assert "Hola" in output_text
    assert "World" in output_text


def test_read_srt_text_falls_back_to_latin1(tmp_path):
    input_path = tmp_path / "input.srt"
    content = (
        "1\n00:00:00,000 --> 00:00:01,000\nBuenas tardes, señor. Un café, por favor.\n"
    )
    input_path.write_bytes(content.encode("latin-1"))

    text = read_srt_text(str(input_path))

    assert "señor" in text
    assert "café" in text


@pytest.mark.asyncio
async def test_translate_srt_file_handles_latin1_encoded_input(tmp_path):
    input_path = tmp_path / "input.srt"
    content = (
        "1\n00:00:00,000 --> 00:00:01,000\nBuenas tardes, señor. Un café, por favor.\n"
    )
    input_path.write_bytes(content.encode("latin-1"))
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Good afternoon, sir. A coffee, please."])

    total_blocks, failed_blocks = await translate_srt_file(
        client, "llama3.1", "es", str(input_path), str(output_path)
    )

    assert total_blocks == 1
    assert failed_blocks == 0
    output_text = output_path.read_text(encoding="utf-8")
    assert "Good afternoon, sir." in output_text
