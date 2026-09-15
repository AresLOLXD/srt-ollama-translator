import httpx
import logging
import pytest
import srt
from datetime import timedelta
from app.translator import (
    SubtitleBlock,
    extract_glossary,
    read_srt_text,
    translate_block,
    translate_srt_file,
)


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
        self.prompts = []

    async def chat(self, model: str, prompt: str) -> str:
        self.prompts.append(prompt)
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


@pytest.mark.asyncio
async def test_translate_block_with_filename_passes_to_prompt():
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    block = make_block(2)
    await translate_block(client, "llama3.1", block, "en", filename="eng.Signs__Songs.eng.srt")
    assert len(client.prompts) == 1
    assert "eng.Signs__Songs.eng.srt" in client.prompts[0]


@pytest.mark.asyncio
async def test_translate_srt_file_with_filename_passes_to_client(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola\n[2] Mundo"])

    await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        filename="eng.DialogueTest.srt",
        block_size=25,
    )

    assert len(client.prompts) == 2
    assert "eng.DialogueTest.srt" in client.prompts[-1]


@pytest.mark.asyncio
async def test_translate_srt_file_reports_block_results(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    results = []

    await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        on_block_result=lambda position, translations, success: results.append(
            (position, translations, success)
        ),
        block_size=25,
    )

    assert results == [(1, {1: "Hola", 2: "Mundo"}, True)]


@pytest.mark.asyncio
async def test_translate_srt_file_reports_failed_block_result(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola"])
    results = []

    await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        on_block_result=lambda position, translations, success: results.append(
            (position, translations, success)
        ),
    )

    assert results == [(1, {1: "Hola"}, False)]


@pytest.mark.asyncio
async def test_translate_srt_file_skips_ollama_for_resumed_blocks(tmp_path):
    input_path = tmp_path / "input.srt"
    lines = []
    for i in range(1, 51):
        start = f"00:00:{i:02d},000"
        end = f"00:00:{i + 1:02d},000"
        lines.append(f"{i}\n{start} --> {end}\nLine {i}\n")
    input_path.write_text("\n".join(lines), encoding="utf-8")
    output_path = tmp_path / "output.srt"

    # Only one scripted response: for block 2 (positions 26-50). Block 1 is
    # "resumed" and must never reach the client.
    block_2_response = "\n".join(f"[{i}] Traducido {i}" for i in range(26, 51))
    client = FakeClient([block_2_response])
    resume_blocks = {1: {i: f"Ya traducido {i}" for i in range(1, 26)}}

    total_blocks, failed_blocks = await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        resume_blocks=resume_blocks,
        block_size=25,
    )

    assert total_blocks == 2
    assert failed_blocks == 0
    assert client.calls == 2
    output_text = output_path.read_text(encoding="utf-8")
    assert "Ya traducido 1" in output_text
    assert "Traducido 26" in output_text


@pytest.mark.asyncio
async def test_translate_block_returns_empty_without_calling_client_when_all_subs_blank():
    client = FakeClient(["should never be used"])
    block = make_block(1)
    block.subs[0].content = "   "
    result = await translate_block(client, "llama3.1", block, "en")
    assert result == {}
    assert client.calls == 0


@pytest.mark.asyncio
async def test_translate_block_ignores_empty_subs_in_expected_indices():
    client = FakeClient(["[2] Mundo"])
    block = make_block(2)
    block.subs[0].content = ""
    result = await translate_block(client, "llama3.1", block, "en")
    assert result == {2: "Mundo"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_translate_srt_file_skips_empty_subtitle_and_still_succeeds(tmp_path):
    # Note: srt.compose() drops subtitles with empty content entirely (they
    # never round-trip back out of srt.parse), so the blank subtitle is
    # written as raw .srt text instead, to guarantee it actually parses back
    # as a subtitle with content == "" alongside the non-blank one.
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n\n\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola"])

    total_blocks, failed_blocks = await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path)
    )

    assert total_blocks == 1
    assert failed_blocks == 0
    assert client.calls == 2
    output_text = output_path.read_text(encoding="utf-8")
    assert "Hola" in output_text


@pytest.mark.asyncio
async def test_translate_block_forwards_context_to_every_retry():
    client = FakeClient(["not matching", "[1] Hola\n[2] Mundo"])
    block = make_block(2)
    await translate_block(client, "llama3.1", block, "en", context=[("Previo", "Previous")])
    assert len(client.prompts) == 2
    assert all("Previo" in p for p in client.prompts)


def test_build_context_picks_last_three_successful_pairs():
    from app.translator import _build_context

    block = make_block(5)
    translations = {1: "Uno", 2: "Dos", 3: "Tres", 4: "Cuatro", 5: "Cinco"}
    result = _build_context(block, translations)
    assert result == [("Line 3", "Tres"), ("Line 4", "Cuatro"), ("Line 5", "Cinco")]


def test_build_context_skips_untranslated_and_empty_subs():
    from app.translator import _build_context

    block = make_block(3)
    block.subs[1].content = ""
    translations = {1: "Uno"}
    result = _build_context(block, translations)
    assert result == [("Line 1", "Uno")]


@pytest.mark.asyncio
async def test_translate_srt_file_passes_context_from_previous_block(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["", "[1] Hola", "[2] Mundo"])

    await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path), block_size=1
    )

    context_prompts = [p for p in client.prompts if "Contexto de continuidad" in p]
    assert len(context_prompts) == 1
    assert "Hello" in context_prompts[0]
    assert "Hola" in context_prompts[0]


@pytest.mark.asyncio
async def test_translate_block_forwards_glossary_to_every_retry():
    client = FakeClient(["not matching", "[1] Hola\n[2] Mundo"])
    block = make_block(2)
    await translate_block(client, "llama3.1", block, "en", glossary=["Jack"])
    assert len(client.prompts) == 2
    assert all("Jack" in p for p in client.prompts)


@pytest.mark.asyncio
async def test_extract_glossary_parses_comma_separated_names():
    client = FakeClient(["Jack, Sarah, Nueva York"])
    subs = make_block(1).subs
    result = await extract_glossary(client, "llama3.1", subs)
    assert result == ["Jack", "Sarah", "Nueva York"]


@pytest.mark.asyncio
async def test_extract_glossary_returns_empty_list_on_client_error():
    class FailingClient:
        async def chat(self, model, prompt):
            raise httpx.RemoteProtocolError("boom")

    subs = make_block(1).subs
    result = await extract_glossary(FailingClient(), "llama3.1", subs)
    assert result == []


@pytest.mark.asyncio
async def test_extract_glossary_returns_empty_list_for_all_blank_subs():
    subs = make_block(1).subs
    subs[0].content = "   "
    result = await extract_glossary(None, "llama3.1", subs)
    assert result == []


@pytest.mark.asyncio
async def test_translate_srt_file_calls_extract_glossary_once_and_uses_it_in_every_block(tmp_path):
    input_path = tmp_path / "input.srt"
    lines = []
    for i in range(1, 51):
        start = f"00:00:{i:02d},000"
        end = f"00:00:{i + 1:02d},000"
        lines.append(f"{i}\n{start} --> {end}\nLine {i}\n")
    input_path.write_text("\n".join(lines), encoding="utf-8")
    output_path = tmp_path / "output.srt"

    glossary_response = "Jack, Sarah"
    block_1_response = "\n".join(f"[{i}] Traducido {i}" for i in range(1, 26))
    block_2_response = "\n".join(f"[{i}] Traducido {i}" for i in range(26, 51))
    client = FakeClient([glossary_response, block_1_response, block_2_response])

    await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path), block_size=25
    )

    assert client.calls == 3
    block_prompts = client.prompts[1:]
    assert all("Jack" in p and "Sarah" in p for p in block_prompts)
