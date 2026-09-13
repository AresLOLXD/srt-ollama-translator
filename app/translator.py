import re
from dataclasses import dataclass
from typing import Callable

import srt

LINE_PATTERN = re.compile(r"^\[(\d+)\]\s*(.*)$")

ENCODING_FALLBACKS = ("utf-8-sig", "cp1252", "latin-1")


def read_srt_text(path: str) -> str:
    """Read a .srt file trying a chain of encodings.

    Tries utf-8-sig, then cp1252, then latin-1 (which never fails, since
    every byte value is a valid latin-1 code point).
    """
    with open(path, "rb") as f:
        raw = f.read()

    for encoding in ENCODING_FALLBACKS[:-1]:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode(ENCODING_FALLBACKS[-1])


@dataclass
class SubtitleBlock:
    subs: list[srt.Subtitle]


def split_into_blocks(subs: list[srt.Subtitle], block_size: int = 25) -> list[SubtitleBlock]:
    return [
        SubtitleBlock(subs=subs[i : i + block_size])
        for i in range(0, len(subs), block_size)
    ]


def build_prompt(block: SubtitleBlock, source_lang: str) -> str:
    source_desc = "el idioma detectado automáticamente" if source_lang == "auto" else source_lang
    lines = "\n".join(f"[{sub.index}] {sub.content}" for sub in block.subs)
    return (
        "Traduce al español los siguientes subtítulos de una película o serie. "
        f"El idioma de origen es {source_desc}. "
        "Devuelve EXACTAMENTE una línea por cada subtítulo recibido, en el formato "
        '"[N] texto traducido", preservando el número N tal cual. '
        "No agregues explicaciones, encabezados ni texto adicional fuera de esas líneas.\n\n"
        f"{lines}"
    )


def parse_translated_response(response: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for line in response.splitlines():
        match = LINE_PATTERN.match(line.strip())
        if match:
            result[int(match.group(1))] = match.group(2)
    return result


async def translate_block(
    client, model: str, block: SubtitleBlock, source_lang: str, max_retries: int = 3
) -> dict[int, str]:
    expected_indices = {sub.index for sub in block.subs}
    translations: dict[int, str] = {}

    for _attempt in range(max_retries):
        prompt = build_prompt(block, source_lang)
        response = await client.chat(model, prompt)
        translations.update(parse_translated_response(response))
        if expected_indices.issubset(translations.keys()):
            break

    return {index: text for index, text in translations.items() if index in expected_indices}


async def translate_srt_file(
    client,
    model: str,
    source_lang: str,
    input_path: str,
    output_path: str,
    on_block_translated: Callable[[int, int], None] | None = None,
    block_size: int = 25,
) -> tuple[int, int]:
    subs = list(srt.parse(read_srt_text(input_path)))

    blocks = split_into_blocks(subs, block_size=block_size)
    translated_by_index: dict[int, str] = {}
    failed_blocks = 0

    for position, block in enumerate(blocks, start=1):
        translations = await translate_block(client, model, block, source_lang)
        expected_indices = {sub.index for sub in block.subs}
        if not expected_indices.issubset(translations.keys()):
            failed_blocks += 1
        translated_by_index.update(translations)
        if on_block_translated:
            on_block_translated(position, len(blocks))

    for sub in subs:
        if sub.index in translated_by_index:
            sub.content = translated_by_index[sub.index]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt.compose(subs))

    return len(blocks), failed_blocks
