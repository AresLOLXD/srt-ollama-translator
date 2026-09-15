import srt
from datetime import timedelta
from app.translator import SubtitleBlock, split_into_blocks, build_prompt, parse_translated_response


def make_subs(count: int) -> list[srt.Subtitle]:
    return [
        srt.Subtitle(
            index=i + 1,
            start=timedelta(seconds=i),
            end=timedelta(seconds=i + 1),
            content=f"Line {i + 1}",
        )
        for i in range(count)
    ]


def test_split_into_blocks_respects_block_size():
    subs = make_subs(55)
    blocks = split_into_blocks(subs, block_size=25)
    assert len(blocks) == 3
    assert [len(b.subs) for b in blocks] == [25, 25, 5]


def test_split_into_blocks_empty_list_returns_no_blocks():
    assert split_into_blocks([], block_size=25) == []


def test_build_prompt_includes_indices_and_content():
    block = SubtitleBlock(subs=make_subs(2))
    prompt = build_prompt(block, source_lang="en")
    assert "[1] Line 1" in prompt
    assert "[2] Line 2" in prompt
    assert "en" in prompt


def test_build_prompt_describes_auto_detect_language():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="auto")
    assert "detectado autom" in prompt.lower()


def test_parse_translated_response_extracts_indexed_lines():
    response = "[1] Hola\n[2] Mundo\n"
    result = parse_translated_response(response)
    assert result == {1: "Hola", 2: "Mundo"}


def test_parse_translated_response_ignores_malformed_lines():
    response = "[1] Hola\nesto no tiene indice\n[3] Adios\n"
    result = parse_translated_response(response)
    assert result == {1: "Hola", 3: "Adios"}


def test_parse_translated_response_empty_string_returns_empty_dict():
    assert parse_translated_response("") == {}


def test_build_prompt_with_filename_includes_filename_in_prompt():
    block = SubtitleBlock(subs=make_subs(2))
    prompt = build_prompt(block, source_lang="en", filename="eng.Signs__Songs.eng.srt")
    assert "eng.Signs__Songs.eng.srt" in prompt


def test_build_prompt_without_filename_matches_original_behavior():
    block = SubtitleBlock(subs=make_subs(2))
    prompt = build_prompt(block, source_lang="en", filename=None)
    # Should not mention "archivo" or "filename" when filename=None
    assert "archivo" not in prompt.lower()
    assert "filename" not in prompt.lower()
    # But should still contain the core prompt structure
    assert "[1] Line 1" in prompt
    assert "[2] Line 2" in prompt
    assert "en" in prompt


def test_build_prompt_instructs_to_preserve_formatting_tags():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="en")
    assert "<i>" in prompt
    assert "conservalas" in prompt.lower() or "consérvalas" in prompt.lower()


def test_build_prompt_skips_empty_subtitles():
    subs = make_subs(2)
    subs[0].content = ""
    block = SubtitleBlock(subs=subs)
    prompt = build_prompt(block, source_lang="en")
    assert "[1]" not in prompt
    assert "[2] Line 2" in prompt
