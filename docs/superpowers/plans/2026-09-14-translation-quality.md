# Mejoras de Calidad de Traducción Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce translation-quality issues in `app/translator.py`/`app/ollama_client.py` — empty subtitles no longer get invented placeholder text, cross-block coherence improves, and the model is less likely to hallucinate or mangle formatting.

**Architecture:** All changes live in `app/translator.py` (prompt construction, per-block translation, and the file-level orchestration loop) plus one payload change in `app/ollama_client.py`. No new files, no DB/schema/route/frontend changes. Each capability (empty-skip, context, glossary, length logging) is added as an incremental, independently-testable layer on top of the existing block-translation loop.

**Tech Stack:** Python, `httpx` (mocked via `httpx.MockTransport` / fake client objects in tests), `pytest` + `pytest-asyncio`, `srt` library.

**Spec:** `docs/superpowers/specs/2026-09-14-translation-quality-design.md`

## Global Constraints

- Fixed temperature: `0.2` (from spec — not configurable, no `config.py`/UI changes).
- Glossary source-text cap: ~4000 characters before sending to the model (from spec).
- Length-warning threshold: translated text longer than `2x` the original character count (from spec).
- Context carried between blocks: last `3` lines with a successful translation (spec says "2-3 líneas" — this plan uses 3).
- Length validation is logging-only: never changes `failed_blocks`, DB rows, or UI (from spec).
- Glossary extraction must never fail the job: any exception is caught and logged, falling back to an empty glossary (from spec).

---

### Task 1: Fixed low temperature in `OllamaClient.chat`

**Files:**
- Modify: `app/ollama_client.py` (the `chat` method)
- Test: `tests/test_ollama_client.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change — `OllamaClient.chat(model, prompt, max_retries=3)` keeps sending the same fields plus a new `"options"` key in the JSON payload. Callers in `app/translator.py` are unaffected.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ollama_client.py`:

```python
@pytest.mark.asyncio
async def test_chat_sends_low_temperature_option():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"message": {"content": "hola"}})

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    await client.chat("llama3.1", "hello")

    assert b'"temperature": 0.2' in captured["body"] or b'"temperature":0.2' in captured["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ollama_client.py::test_chat_sends_low_temperature_option -v`
Expected: FAIL (assertion fails — no `"temperature"` in the request body yet).

- [ ] **Step 3: Write minimal implementation**

In `app/ollama_client.py`, replace the `chat` method body:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ollama_client.py -v`
Expected: all PASS, including the new test and the existing ones (existing tests only check `b"llama3.1"` / `b"translate: hello world"` substrings, unaffected by the added key).

- [ ] **Step 5: Commit**

```bash
git add app/ollama_client.py tests/test_ollama_client.py
git commit -m "feat: use a fixed low temperature for Ollama chat requests"
```

---

### Task 2: Preserve formatting tags instruction in `build_prompt`

**Files:**
- Modify: `app/translator.py` (the `build_prompt` function)
- Test: `tests/test_translator_blocks.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_prompt(block, source_lang, filename=None)` — same signature, prompt text gains one fixed sentence. No other function changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_translator_blocks.py`:

```python
def test_build_prompt_instructs_to_preserve_formatting_tags():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="en")
    assert "<i>" in prompt
    assert "conservalas" in prompt.lower() or "consérvalas" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_translator_blocks.py::test_build_prompt_instructs_to_preserve_formatting_tags -v`
Expected: FAIL (no mention of `<i>` in the current prompt).

- [ ] **Step 3: Write minimal implementation**

In `app/translator.py`, replace the `build_prompt` function:

```python
def build_prompt(block: SubtitleBlock, source_lang: str, filename: str | None = None) -> str:
    source_desc = "el idioma detectado automáticamente" if source_lang == "auto" else source_lang
    lines = "\n".join(f"[{sub.index}] {sub.content}" for sub in block.subs)
    filename_line = f"Nombre del archivo: {filename}. " if filename else ""
    return (
        "Traduce al español los siguientes subtítulos de una película o serie. "
        f"{filename_line}"
        f"El idioma de origen es {source_desc}. "
        "Devuelve EXACTAMENTE una línea por cada subtítulo recibido, en el formato "
        '"[N] texto traducido", preservando el número N tal cual. '
        "Si el texto contiene etiquetas de formato como <i>, </i>, <b>, </b> o saltos de "
        "línea, conservalas tal cual en la traducción. "
        "No agregues explicaciones, encabezados ni texto adicional fuera de esas líneas.\n\n"
        f"{lines}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_blocks.py
git commit -m "feat: instruct the model to preserve formatting tags when translating"
```

---

### Task 3: Skip empty subtitles

**Files:**
- Modify: `app/translator.py` (`build_prompt`, `translate_block`, `translate_srt_file`)
- Test: `tests/test_translator_blocks.py`, `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: `build_prompt`/`translate_block` from Task 2 (same signatures).
- Produces: `build_prompt` skips subs with blank `content` when building `[N]` lines. `translate_block` computes `expected_indices` only from non-blank subs, and returns `{}` immediately (no `client.chat` call) when a block has none. `translate_srt_file`'s own `expected_indices` computation matches the same filter, so an all-blank block is never counted as failed.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_translator_blocks.py`:

```python
def test_build_prompt_skips_empty_subtitles():
    subs = make_subs(2)
    subs[0].content = ""
    block = SubtitleBlock(subs=subs)
    prompt = build_prompt(block, source_lang="en")
    assert "[1]" not in prompt
    assert "[2] Line 2" in prompt
```

Add to `tests/test_translator_translate.py`:

```python
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
    subs = [
        srt.Subtitle(index=1, start=timedelta(seconds=0), end=timedelta(seconds=1), content="Hello"),
        srt.Subtitle(index=2, start=timedelta(seconds=1), end=timedelta(seconds=2), content=""),
    ]
    input_path = tmp_path / "input.srt"
    input_path.write_text(srt.compose(subs), encoding="utf-8")
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola"])

    total_blocks, failed_blocks = await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path)
    )

    assert total_blocks == 1
    assert failed_blocks == 0
    assert client.calls == 1
    output_text = output_path.read_text(encoding="utf-8")
    assert "Hola" in output_text
```

`tests/test_translator_translate.py` needs `srt` imported (already is, at the top) — no new import required.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: the 4 new tests FAIL (empty subs currently generate a `[N]` line and count toward `expected_indices`).

- [ ] **Step 3: Write minimal implementation**

In `app/translator.py`, replace `build_prompt`:

```python
def build_prompt(block: SubtitleBlock, source_lang: str, filename: str | None = None) -> str:
    source_desc = "el idioma detectado automáticamente" if source_lang == "auto" else source_lang
    lines = "\n".join(
        f"[{sub.index}] {sub.content}" for sub in block.subs if sub.content.strip() != ""
    )
    filename_line = f"Nombre del archivo: {filename}. " if filename else ""
    return (
        "Traduce al español los siguientes subtítulos de una película o serie. "
        f"{filename_line}"
        f"El idioma de origen es {source_desc}. "
        "Devuelve EXACTAMENTE una línea por cada subtítulo recibido, en el formato "
        '"[N] texto traducido", preservando el número N tal cual. '
        "Si el texto contiene etiquetas de formato como <i>, </i>, <b>, </b> o saltos de "
        "línea, conservalas tal cual en la traducción. "
        "No agregues explicaciones, encabezados ni texto adicional fuera de esas líneas.\n\n"
        f"{lines}"
    )
```

Replace `translate_block`:

```python
async def translate_block(
    client,
    model: str,
    block: SubtitleBlock,
    source_lang: str,
    max_retries: int = 3,
    filename: str | None = None,
) -> dict[int, str]:
    expected_indices = {sub.index for sub in block.subs if sub.content.strip() != ""}
    if not expected_indices:
        return {}
    translations: dict[int, str] = {}

    for _attempt in range(max_retries):
        prompt = build_prompt(block, source_lang, filename=filename)
        response = await client.chat(model, prompt)
        translations.update(parse_translated_response(response))
        if expected_indices.issubset(translations.keys()):
            break

    return {index: text for index, text in translations.items() if index in expected_indices}
```

In `translate_srt_file`, replace the line
```python
        expected_indices = {sub.index for sub in block.subs}
```
with
```python
        expected_indices = {sub.index for sub in block.subs if sub.content.strip() != ""}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_blocks.py tests/test_translator_translate.py
git commit -m "feat: skip empty subtitles instead of letting the model invent placeholder text"
```

---

### Task 4: Context of continuity between blocks

**Files:**
- Modify: `app/translator.py` (`build_prompt`, `translate_block`, new `_build_context` helper, `translate_srt_file`)
- Test: `tests/test_translator_blocks.py`, `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: `build_prompt`/`translate_block` from Task 3.
- Produces: `build_prompt(block, source_lang, filename=None, context=None)` where `context: list[tuple[str, str]] | None` is a list of `(original, translated)` pairs. `translate_block(..., context=None)` forwards it unchanged on every retry. `_build_context(block, translations, max_lines=3) -> list[tuple[str, str]]` (module-level helper, not exported elsewhere) picks up to the last 3 `(original, translated)` pairs from a block's successful translations, skipping blank-content subs. `translate_srt_file` threads `context` from one block to the next.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_translator_blocks.py`:

```python
def test_build_prompt_includes_context_section():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="en", context=[("Hola", "Hi")])
    assert "Contexto de continuidad" in prompt
    assert "Hola" in prompt
    assert "Hi" in prompt
    assert "NO las traduzcas de nuevo" in prompt


def test_build_prompt_without_context_omits_section():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="en")
    assert "Contexto de continuidad" not in prompt
```

Add to `tests/test_translator_translate.py`:

```python
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
    client = FakeClient(["[1] Hola", "[2] Mundo"])

    await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path), block_size=1
    )

    context_prompts = [p for p in client.prompts if "Contexto de continuidad" in p]
    assert len(context_prompts) == 1
    assert "Hello" in context_prompts[0]
    assert "Hola" in context_prompts[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: the new tests FAIL (`context` parameter doesn't exist yet, `_build_context` doesn't exist).

- [ ] **Step 3: Write minimal implementation**

In `app/translator.py`, replace `build_prompt`:

```python
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
```

Replace `translate_block`:

```python
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
```

Add a new module-level helper (place it right above `translate_srt_file`):

```python
def _build_context(
    block: SubtitleBlock, translations: dict[int, str], max_lines: int = 3
) -> list[tuple[str, str]]:
    pairs = [
        (sub.content, translations[sub.index])
        for sub in block.subs
        if sub.index in translations and sub.content.strip() != ""
    ]
    return pairs[-max_lines:]
```

In `translate_srt_file`, add `context: list[tuple[str, str]] | None = None` right after the `failed_blocks = 0` line, change the `translate_block` call to pass it, and set it after the if/else. The loop becomes:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_blocks.py tests/test_translator_translate.py
git commit -m "feat: carry translated context from one block into the next for coherence"
```

---

### Task 5: Glossary plumbing in `build_prompt`/`translate_block`

**Files:**
- Modify: `app/translator.py` (`build_prompt`, `translate_block`)
- Test: `tests/test_translator_blocks.py`, `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: `build_prompt`/`translate_block` from Task 4.
- Produces: `build_prompt(block, source_lang, filename=None, context=None, glossary=None)` where `glossary: list[str] | None`. `translate_block(..., glossary=None)` forwards it unchanged on every retry. Nothing calls this with real data yet — that's Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_translator_blocks.py`:

```python
def test_build_prompt_includes_glossary_terms():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="en", glossary=["Jack", "Sarah"])
    assert "Jack" in prompt
    assert "Sarah" in prompt
    assert "sin traducir" in prompt


def test_build_prompt_without_glossary_omits_instruction():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="en")
    assert "sin traducir" not in prompt
```

Add to `tests/test_translator_translate.py`:

```python
@pytest.mark.asyncio
async def test_translate_block_forwards_glossary_to_every_retry():
    client = FakeClient(["not matching", "[1] Hola\n[2] Mundo"])
    block = make_block(2)
    await translate_block(client, "llama3.1", block, "en", glossary=["Jack"])
    assert len(client.prompts) == 2
    assert all("Jack" in p for p in client.prompts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: the 3 new tests FAIL (`glossary` parameter doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `app/translator.py`, replace `build_prompt`:

```python
def build_prompt(
    block: SubtitleBlock,
    source_lang: str,
    filename: str | None = None,
    context: list[tuple[str, str]] | None = None,
    glossary: list[str] | None = None,
) -> str:
    source_desc = "el idioma detectado automáticamente" if source_lang == "auto" else source_lang
    lines = "\n".join(
        f"[{sub.index}] {sub.content}" for sub in block.subs if sub.content.strip() != ""
    )
    filename_line = f"Nombre del archivo: {filename}. " if filename else ""

    glossary_line = ""
    if glossary:
        glossary_line = f"Mantené estos nombres o términos sin traducir: {', '.join(glossary)}. "

    instructions = (
        "Traduce al español los siguientes subtítulos de una película o serie. "
        f"{filename_line}"
        f"El idioma de origen es {source_desc}. "
        "Devuelve EXACTAMENTE una línea por cada subtítulo recibido, en el formato "
        '"[N] texto traducido", preservando el número N tal cual. '
        "Si el texto contiene etiquetas de formato como <i>, </i>, <b>, </b> o saltos de "
        "línea, conservalas tal cual en la traducción. "
        f"{glossary_line}"
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
```

Replace `translate_block`:

```python
async def translate_block(
    client,
    model: str,
    block: SubtitleBlock,
    source_lang: str,
    max_retries: int = 3,
    filename: str | None = None,
    context: list[tuple[str, str]] | None = None,
    glossary: list[str] | None = None,
) -> dict[int, str]:
    expected_indices = {sub.index for sub in block.subs if sub.content.strip() != ""}
    if not expected_indices:
        return {}
    translations: dict[int, str] = {}

    for _attempt in range(max_retries):
        prompt = build_prompt(
            block, source_lang, filename=filename, context=context, glossary=glossary
        )
        response = await client.chat(model, prompt)
        translations.update(parse_translated_response(response))
        if expected_indices.issubset(translations.keys()):
            break

    return {index: text for index, text in translations.items() if index in expected_indices}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_blocks.py tests/test_translator_translate.py
git commit -m "feat: let build_prompt and translate_block accept a proper-noun glossary"
```

---

### Task 6: Extract and wire in the per-file glossary

**Files:**
- Modify: `app/translator.py` (new `extract_glossary` function, `import logging`, `translate_srt_file` wiring)
- Modify (fix existing tests broken by the new call): `tests/test_translator_translate.py`
- Test (new): `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: `translate_block(..., glossary=None)` from Task 5.
- Produces: `async def extract_glossary(client, model: str, subs: list[srt.Subtitle], filename: str | None = None) -> list[str]`. `translate_srt_file` calls it once per file (only when there's at least one block) and passes the result to every `translate_block` call for that file.

**Important — this task changes call counts for existing tests.** `translate_srt_file` now makes one extra `client.chat` call (the glossary extraction) before any block translation. Fix these 3 existing tests plus the one added in Task 4, as shown below.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_translator_translate.py` (needs `import httpx` and `import logging` added at the top of the file, alongside the existing imports):

```python
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
```

Now fix the 3 pre-existing tests that break because of the extra call:

Replace the `test_translate_srt_file_with_filename_passes_to_client` test body's assertions:

```python
    assert len(client.prompts) == 2
    assert "eng.DialogueTest.srt" in client.prompts[-1]
```

Replace the `test_translate_srt_file_skips_ollama_for_resumed_blocks` test's assertion:

```python
    assert client.calls == 2
```

Replace the `test_translate_srt_file_passes_context_from_previous_block` test's client construction (from Task 4) to account for the glossary call consuming the first scripted response:

```python
    client = FakeClient(["", "[1] Hola", "[2] Mundo"])
```

(the rest of that test is unchanged — the assertions already filter by `"Contexto de continuidad" in p`, so they remain correct once the responses line up with the right calls).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_translate.py -v`
Expected: the 4 new tests FAIL (`extract_glossary` doesn't exist); the 3 fixed tests FAIL against the *current* code (since the wiring isn't in yet, they now assert counts/positions that don't match pre-Task-6 behavior) — that's expected, both sides land together in Step 4.

- [ ] **Step 3: Write minimal implementation**

In `app/translator.py`, add `import logging` at the top (alongside `import re`), and add a module-level logger right after the existing `LINE_PATTERN`/`ENCODING_FALLBACKS` constants:

```python
logger = logging.getLogger(__name__)
```

Add `extract_glossary` (place it above `translate_block`):

```python
GLOSSARY_TEXT_LIMIT = 4000


async def extract_glossary(
    client, model: str, subs: list[srt.Subtitle], filename: str | None = None
) -> list[str]:
    text = "\n".join(sub.content for sub in subs if sub.content.strip() != "")
    if not text:
        return []
    text = text[:GLOSSARY_TEXT_LIMIT]

    filename_line = f"Nombre del archivo: {filename}. " if filename else ""
    prompt = (
        "A continuación hay texto de subtítulos de una película o serie. "
        f"{filename_line}"
        "Listá los nombres propios (personajes, lugares) que aparecen, separados por "
        "comas, sin explicaciones ni texto adicional.\n\n"
        f"{text}"
    )

    try:
        response = await client.chat(model, prompt)
    except Exception:
        logger.warning("No se pudo extraer el glosario para %s", filename, exc_info=True)
        return []

    seen: set[str] = set()
    names: list[str] = []
    for name in re.split(r"[,\n]", response):
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names
```

In `translate_srt_file`, add the glossary extraction right after `failed_blocks = 0` and `context = None`, and pass `glossary=glossary` into the `translate_block` call:

```python
    context: list[tuple[str, str]] | None = None

    glossary: list[str] = []
    if blocks:
        glossary = await extract_glossary(client, model, subs, filename=filename)

    for position, block in enumerate(blocks, start=1):
        expected_indices = {sub.index for sub in block.subs if sub.content.strip() != ""}

        if position in resume_blocks:
            translations = resume_blocks[position]
        else:
            translations = await translate_block(
                client, model, block, source_lang,
                filename=filename, context=context, glossary=glossary,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py tests/test_ollama_client.py -v`
Expected: all PASS (full suite, since this task also touched previously-passing tests).

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_translate.py
git commit -m "feat: extract a per-file proper-noun glossary and use it in every block prompt"
```

---

### Task 7: Length-mismatch warning logging

**Files:**
- Modify: `app/translator.py` (new `_log_length_warnings` helper, `translate_srt_file` wiring)
- Test: `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: `logger` from Task 6.
- Produces: `_log_length_warnings(block: SubtitleBlock, translations: dict[int, str], filename: str | None) -> None` (module-level helper, side-effect only — logs, returns nothing). Called from `translate_srt_file` only for freshly-translated blocks (not `resume_blocks` ones).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_translator_translate.py` (needs `import logging` at the top, added in Task 6):

```python
def test_log_length_warnings_flags_translation_more_than_double_original():
    from app.translator import _log_length_warnings
    import logging as logging_module

    block = make_block(1)
    block.subs[0].content = "Hi"
    translations = {1: "H" * 10}

    logger = logging_module.getLogger("app.translator")
    records = []
    handler = logging_module.Handler()
    handler.emit = lambda record: records.append(record)
    logger.addHandler(handler)
    logger.setLevel(logging_module.WARNING)
    try:
        _log_length_warnings(block, translations, filename="test.srt")
    finally:
        logger.removeHandler(handler)

    assert len(records) == 1
    assert "sospechosamente larga" in records[0].getMessage()


def test_log_length_warnings_ignores_normal_length_translation():
    from app.translator import _log_length_warnings
    import logging as logging_module

    block = make_block(1)
    block.subs[0].content = "Hello there"
    translations = {1: "Hola"}

    logger = logging_module.getLogger("app.translator")
    records = []
    handler = logging_module.Handler()
    handler.emit = lambda record: records.append(record)
    logger.addHandler(handler)
    logger.setLevel(logging_module.WARNING)
    try:
        _log_length_warnings(block, translations, filename="test.srt")
    finally:
        logger.removeHandler(handler)

    assert records == []


@pytest.mark.asyncio
async def test_translate_srt_file_logs_warning_for_abnormally_long_translation(tmp_path, caplog):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8"
    )
    output_path = tmp_path / "output.srt"
    long_translation = "Hola " * 20
    client = FakeClient(["", f"[1] {long_translation}"])

    with caplog.at_level(logging.WARNING, logger="app.translator"):
        total_blocks, failed_blocks = await translate_srt_file(
            client, "llama3.1", "en", str(input_path), str(output_path)
        )

    assert failed_blocks == 0
    assert "sospechosamente larga" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_translate.py -v`
Expected: the 3 new tests FAIL (`_log_length_warnings` doesn't exist yet, no warning is ever logged).

- [ ] **Step 3: Write minimal implementation**

In `app/translator.py`, add `_log_length_warnings` (place it right above `translate_srt_file`, after `_build_context`):

```python
def _log_length_warnings(
    block: SubtitleBlock, translations: dict[int, str], filename: str | None
) -> None:
    for sub in block.subs:
        translated = translations.get(sub.index)
        if translated is None:
            continue
        original = sub.content
        if original and len(translated) > len(original) * 2:
            logger.warning(
                "Traducción sospechosamente larga en %s índice %s: "
                "original=%r (%d chars), traducción=%r (%d chars)",
                filename, sub.index, original, len(original), translated, len(translated),
            )
```

In `translate_srt_file`, call it right after `on_block_result`, inside the `else` branch (only for freshly-translated blocks):

```python
        if position in resume_blocks:
            translations = resume_blocks[position]
        else:
            translations = await translate_block(
                client, model, block, source_lang,
                filename=filename, context=context, glossary=glossary,
            )
            success = expected_indices.issubset(translations.keys())
            if on_block_result:
                on_block_result(position, translations, success)
            _log_length_warnings(block, translations, filename)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_blocks.py tests/test_translator_translate.py tests/test_ollama_client.py -v`
Expected: all PASS (full suite).

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_translate.py
git commit -m "feat: log a warning when a translated line is abnormally longer than the original"
```

---

## Final verification

After Task 7, run the full test suite once more to confirm nothing regressed across all 7 tasks:

```bash
uv run pytest -v
```

Expected: all tests PASS.
