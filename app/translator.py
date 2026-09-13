import re
from dataclasses import dataclass

import srt

LINE_PATTERN = re.compile(r"^\[(\d+)\]\s*(.*)$")


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
