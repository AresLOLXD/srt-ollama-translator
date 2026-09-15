# Mejoras de calidad de traducción

## Contexto y motivación

Se detectó que, cuando un subtítulo del `.srt` original no tiene texto
(`content` vacío o solo espacios), el modelo a veces "rellena" ese índice con
un placeholder inventado (p. ej. `(vacío)`, `(sin texto)`) en vez de devolver
nada, porque `build_prompt` igual genera una línea `[N]` sin contenido para
traducir.

Al investigar ese caso puntual surgieron otras oportunidades de mejora en la
misma zona del código (calidad y coherencia de la traducción por bloques), y
se decidió abordarlas juntas por tocar las mismas funciones:

1. Subtítulos vacíos rellenados con texto inventado por el modelo.
2. Bloques de 25 subtítulos se traducen sin saber cómo terminó el bloque
   anterior, lo que puede romper coherencia (pronombres, tono) en diálogos
   que cruzan el corte de bloque.
3. El modelo puede alucinar o desviarse del texto original por temperatura
   por defecto alta.
4. Etiquetas de formato (`<i>`, `<b>`, saltos de línea) a veces se pierden o
   rompen al traducir.
5. Un mismo nombre propio puede traducirse de forma distinta en distintos
   bloques del mismo archivo (falta de glosario compartido).
6. No hay ninguna señal, ni siquiera en logs, de traducciones
   anormalmente más largas que el original (riesgo de sync/legibilidad en
   pantalla).

## Alcance

Cubre: exclusión de subtítulos vacíos del prompt, contexto de continuidad
entre bloques, temperatura fija más baja, instrucción de preservar tags de
formato, extracción de glosario de nombres propios por archivo, y logging de
advertencia por longitud de traducción. Todo dentro de `app/translator.py` y
`app/ollama_client.py`.

Queda fuera de alcance: hacer configurable la temperatura desde la UI,
mostrar advertencias de longitud en la UI o DB, y cachear/persistir el
glosario entre archivos o jobs (se recalcula por archivo, en memoria).

## Temperatura fija (`app/ollama_client.py`)

`OllamaClient.chat()` agrega `"options": {"temperature": 0.2}` al payload
JSON enviado a `POST /api/chat`. Valor fijo en el código, sin nuevo parámetro
de configuración ni cambio en `app/config.py`.

## Subtítulos vacíos (`app/translator.py`)

- `build_prompt`: solo genera una línea `[N] texto` por cada sub cuyo
  `content.strip()` no sea `""`. Los subs vacíos no aparecen en el prompt.
- `translate_block`: `expected_indices` se calcula únicamente sobre subs con
  contenido no vacío. Si el bloque completo queda sin índices esperados
  (todos sus subs están vacíos), la función devuelve `{}` inmediatamente,
  sin llamar a `client.chat` ni consumir reintentos.
- `translate_srt_file`: sin cambios de comportamiento — como los subs vacíos
  nunca entran en `translated_by_index`, conservan su `content` original
  (vacío) al componer el archivo de salida. Un bloque compuesto solo por
  subs vacíos ya no cuenta como bloque fallido.

## Contexto de continuidad entre bloques (`app/translator.py`)

- `build_prompt` gana un parámetro opcional `context: list[tuple[str, str]]
  | None` (pares `texto_original, texto_traducido`). Si viene con datos, se
  antepone a las líneas a traducir un bloque de texto:

  ```
  Contexto de continuidad (últimas líneas ya traducidas del bloque anterior,
  solo de referencia para mantener tono/coherencia — NO las traduzcas de
  nuevo):
  "<original>" → "<traducción>"
  ...

  Ahora traduce los siguientes subtítulos nuevos:
  ```

  Sin `context` (o lista vacía), el prompt se genera igual que hoy — no
  rompe los tests existentes de `build_prompt`.

- `translate_block` recibe `context` y lo reenvía a `build_prompt` en cada
  intento de retry del mismo bloque (el contexto no cambia entre retries).

- `translate_srt_file`: después de procesar cada bloque, toma las últimas
  2-3 líneas con traducción exitosa de ese bloque (ignorando subs vacíos —
  nunca tienen traducción) como pares `(original, traducción)`, y las pasa
  como `context` a la llamada de `translate_block` del bloque siguiente. El
  primer bloque del archivo se traduce sin contexto (`context=None`).

  En modo `resume_blocks`, el contexto para el bloque siguiente se
  reconstruye de la misma forma a partir de `resume_blocks[position]`, para
  que reanudar un job no pierda continuidad entre el bloque reanudado y el
  próximo bloque nuevo.

## Preservar etiquetas de formato (`app/translator.py`)

`build_prompt` agrega una frase fija a las instrucciones existentes,
indicando conservar tal cual etiquetas de formato (`<i>`, `</i>`, `<b>`,
`</b>`) y saltos de línea dentro de un mismo subtítulo.

## Glosario de nombres propios (`app/translator.py`)

Nueva función:

```python
async def extract_glossary(
    client, model: str, subs: list[srt.Subtitle], filename: str | None = None
) -> list[str]:
```

- Junta el `content` de los subs con texto no vacío en un solo bloque de
  texto, truncado a un límite fijo (~4000 caracteres) para no generar un
  prompt desproporcionado en archivos largos.
- Envía un único prompt a Ollama (vía `client.chat`, misma temperatura fija
  de 0.2) pidiendo que liste nombres propios (personajes, lugares) presentes
  en el texto, separados por coma, sin explicaciones adicionales.
- Parsea la respuesta dividiendo por coma y/o salto de línea, recorta
  espacios, descarta entradas vacías, y devuelve una lista de strings
  únicos.
- Si la llamada lanza una excepción (`httpx.TransportError` u otra) o la
  respuesta no se puede parsear en absolutamente nada útil, se captura, se
  loguea con `logger.warning`, y se devuelve `[]`. Este paso **nunca** debe
  hacer fallar el job — es una mejora de calidad, no un requisito.

`translate_srt_file`:

- Llama a `extract_glossary` una única vez al principio, antes del loop de
  bloques, solo si `blocks` no está vacío. El resultado (lista de strings,
  posiblemente vacía) se guarda en una variable local y se pasa igual a
  cada llamada de `translate_block` del archivo — no cambia entre bloques.

`build_prompt` gana un parámetro opcional `glossary: list[str] | None`. Si
no está vacío, agrega una línea de instrucción: "Mantené estos nombres o
términos sin traducir: {lista separada por comas}".

## Validación de longitud (solo logging) (`app/translator.py`)

- Se agrega `logger = logging.getLogger(__name__)` al módulo.
- En `translate_srt_file`, después de que un bloque devuelve sus
  traducciones (nuevas, no las de `resume_blocks`), por cada índice
  traducido con original no vacío: si `len(traducido) > len(original) * 2`,
  se emite `logger.warning` incluyendo `filename`, índice, y ambos textos.
- No afecta el resultado del bloque, `failed_blocks`, la DB ni la UI. Es
  puramente diagnóstico, para revisar en logs del worker si hace falta
  investigar un archivo particular.

## Testing

TDD por pieza, siguiendo el patrón existente de transporte HTTP falso
(`tests/test_ollama_client.py`, `tests/test_translator_translate.py`):

- `app/ollama_client.py`: verificar que el payload enviado a `/api/chat`
  incluye `"options": {"temperature": 0.2}`.
- `app/translator.py` — `build_prompt`:
  - un sub con `content=""` no genera línea `[N]` en el prompt.
  - con `context` no vacío, el prompt incluye la sección de contexto y la
    frase "NO las traduzcas de nuevo"; sin `context`, no aparece esa
    sección.
  - el prompt siempre incluye la instrucción de preservar tags de formato.
  - con `glossary` no vacío, el prompt incluye la lista de nombres a no
    traducir; sin glosario, no aparece esa línea.
- `app/translator.py` — `translate_block`:
  - bloque compuesto solo por subs vacíos: no se llama a `client.chat`
    (contar invocaciones sobre el transporte falso), devuelve `{}`.
  - bloque mixto (vacíos + con texto): solo se piden traducciones para los
    índices con texto, y el bloque se marca exitoso aunque el modelo no
    devuelva nada para los índices vacíos.
- `app/translator.py` — `extract_glossary`:
  - transporte falso que responde con una lista de nombres → se parsea
    correctamente en una lista de strings únicos.
  - transporte falso que lanza error de conexión → devuelve `[]` sin
    propagar la excepción.
- `app/translator.py` — `translate_srt_file` (integración):
  - dos bloques: el prompt del segundo bloque contiene contexto derivado de
    las traducciones del primero.
  - se llama a `extract_glossary` una sola vez por archivo (no por bloque),
    y el glosario resultante aparece en el prompt de todos los bloques del
    archivo.
  - con un sub cuya traducción simulada es mucho más larga que el original,
    se emite un `logger.warning` (usar `caplog` de pytest) y el bloque no se
    marca como fallido por eso.
