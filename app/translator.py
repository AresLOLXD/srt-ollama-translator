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


def build_prompt(
    block: SubtitleBlock,
    source_lang: str,
    filename: str | None = None,
    context: list[tuple[str, str]] | None = None,
) -> str:
    source_desc = "el idioma detectado automáticamente" if source_lang == "auto" else source_lang
    lines = "\n".join(
        f"[{sub.index}] {sub.content}" for sub in block.subs if sub.content.strip() != ""
    )
    filename_line = f"Nombre del archivo: {filename}. " if filename else ""

    instructions = (
        "Traduce al español los siguientes subtítulos de una película o serie. "
        f"{filename_line}"
        f"El idioma de origen es {source_desc}. "
        "Devuelve EXACTAMENTE una línea por cada subtítulo recibido, en el formato "
        '"[N] texto traducido", preservando el número N tal cual. '
        "Si el texto contiene etiquetas de formato como <i>, </i>, <b>, </b> o saltos de "
        "línea, conservalas tal cual en la traducción. "
        "No agregues explicaciones, encabezados ni texto adicional fuera de esas líneas."
    )

    context_block = ""
    if context:
        context_lines = "\n".join(
            f'"{original}" -> "{translated}"' for original, translated in context
        )
        context_block = (
            "\n\nContexto de continuidad (últimas líneas ya traducidas del bloque anterior, "
            "solo de referencia para mantener tono/coherencia -- NO las traduzcas de nuevo):\n"
            f"{context_lines}\n\nAhora traduce los siguientes subtítulos nuevos:"
        )

    return f"{instructions}{context_block}\n\n{lines}"


def parse_translated_response(response: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for line in response.splitlines():
        match = LINE_PATTERN.match(line.strip())
        if match:
            result[int(match.group(1))] = match.group(2)
    return result


async def translate_block(
    client,
    model: str,
    block: SubtitleBlock,
    source_lang: str,
    max_retries: int = 3,
    filename: str | None = None,
    context: list[tuple[str, str]] | None = None,
) -> dict[int, str]:
    expected_indices = {sub.index for sub in block.subs if sub.content.strip() != ""}
    if not expected_indices:
        return {}
    translations: dict[int, str] = {}

    for _attempt in range(max_retries):
        prompt = build_prompt(block, source_lang, filename=filename, context=context)
        response = await client.chat(model, prompt)
        translations.update(parse_translated_response(response))
        if expected_indices.issubset(translations.keys()):
            break

    return {index: text for index, text in translations.items() if index in expected_indices}


def _build_context(
    block: SubtitleBlock, translations: dict[int, str], max_lines: int = 3
) -> list[tuple[str, str]]:
    pairs = [
        (sub.content, translations[sub.index])
        for sub in block.subs
        if sub.index in translations and sub.content.strip() != ""
    ]
    return pairs[-max_lines:]


async def translate_srt_file(
    client,
    model: str,
    source_lang: str,
    input_path: str,
    output_path: str,
    on_block_translated: Callable[[int, int], None] | None = None,
    on_block_result: Callable[[int, dict[int, str], bool], None] | None = None,
    resume_blocks: dict[int, dict[int, str]] | None = None,
    block_size: int = 25,
    filename: str | None = None,
) -> tuple[int, int]:
    subs = list(srt.parse(read_srt_text(input_path)))

    blocks = split_into_blocks(subs, block_size=block_size)
    resume_blocks = resume_blocks or {}
    translated_by_index: dict[int, str] = {}
    failed_blocks = 0
    context: list[tuple[str, str]] | None = None

    for position, block in enumerate(blocks, start=1):
        expected_indices = {sub.index for sub in block.subs if sub.content.strip() != ""}

        if position in resume_blocks:
            translations = resume_blocks[position]
        else:
            translations = await translate_block(
                client, model, block, source_lang, filename=filename, context=context
            )
            success = expected_indices.issubset(translations.keys())
            if on_block_result:
                on_block_result(position, translations, success)

        context = _build_context(block, translations)

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
