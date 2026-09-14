# Resiliencia de jobs: reintentos, reanudación, cancelación y descarga parcial

## Contexto y causa raíz

Un reinicio programado del contenedor tumbó la conexión a Ollama en medio de
una traducción (`httpx.RemoteProtocolError: Server disconnected without
sending a response`). El error se propagó sin capturar hasta
`queue_worker.process_job`, cuyo `except Exception` genérico marcó **todo el
job** como `failed`. Como `output.zip` solo se genera al final de un job
exitoso, y `/api/jobs/{id}/download` solo permite descargar jobs
`completed`/`completed_with_errors`, todo el trabajo ya hecho (archivos ya
traducidos, bloques ya traducidos dentro del archivo en curso) quedó
inaccesible y sin forma de reanudarse. `reset_stale_processing_jobs` no
ayuda aquí porque el `except` alcanzó a marcar el job como `failed` antes de
que el proceso terminara de apagarse.

Causas de fondo a resolver:

1. Un error de red transitorio se trata igual que un fallo permanente.
2. No existe ningún mecanismo de reintento a nivel job.
3. El progreso (archivos completos y bloques traducidos dentro del archivo en
   curso) no se persiste de forma que permita reanudar sin repetir trabajo.
4. No hay forma de descargar lo que ya se tradujo si el job no terminó del
   todo.
5. No hay forma de que el usuario cancele un job en curso.

## Alcance

Cubre: reintento de red transitorio, reintento automático de jobs a nivel
job (con límite), reanudación exacta por bloque, cancelación manual
cooperativa, y descarga de los archivos ya completados dentro de un job que
no terminó. Queda fuera de alcance: descarga de contenido parcial de un
archivo a medio traducir (solo se descargan archivos `.srt` completos).

## Modelo de datos (`app/db.py`)

### `jobs` — columnas nuevas

- `retry_count INTEGER NOT NULL DEFAULT 0` — reintentos automáticos ya
  consumidos por fallos de excepción (no por bloques fallidos).
- `cancel_requested INTEGER NOT NULL DEFAULT 0` — bandera que el endpoint de
  stop enciende y que el worker consulta cooperativamente.

`status` gana un valor nuevo: `stopped` (cancelado por el usuario). Los
valores existentes (`pending`, `processing`, `completed`,
`completed_with_errors`, `failed`) se mantienen. `failed` deja de ser un
estado terminal desde la perspectiva del usuario: sigue siendo reanudable
manualmente vía `/resume`.

### `job_files` — sin cambios de columnas

`status`/`total_blocks`/`translated_blocks`/`failed_blocks` mantienen su
significado actual.

### Tabla nueva `job_file_blocks`

```sql
CREATE TABLE job_file_blocks (
    job_file_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    translations_json TEXT NOT NULL,
    success INTEGER NOT NULL,
    PRIMARY KEY (job_file_id, position)
);
```

- Se hace upsert de una fila después de intentar cada bloque (éxito o
  fracaso), con `translations_json` = JSON de `{indice: texto}` obtenido en
  ese intento y `success` = 1 si el bloque quedó completo (todos los índices
  esperados presentes).
- Es la única fuente de verdad de "qué bloques ya están resueltos". No se
  infiere por conteo (`translated_blocks`) ni por contenido del archivo de
  salida, para evitar falsos positivos.
- Se borran las filas de un `job_file` cuando este termina
  (`completed`/`completed_with_errors`), para no acumular filas muertas.

Funciones nuevas en `db.py`: `upsert_job_file_block`,
`get_completed_job_file_blocks(job_file_id) -> dict[int, dict[int, str]]`
(solo filas con `success=1`), `delete_job_file_blocks(job_file_id)`,
`increment_job_retry_count`, `set_job_cancel_requested`,
`get_job_cancel_requested`.

`reset_stale_processing_jobs` (recuperación ante caída dura del proceso, sin
excepción capturada) deja de resetear `translated_blocks`/`failed_blocks` a
0 — el conteo y las filas de `job_file_blocks` ya reflejan el progreso real
y deben preservarse para que la reanudación no repita trabajo. Solo vuelve
`processing → pending` a nivel job y job_file.

## Reintento de red transitorio (`app/ollama_client.py`)

`OllamaClient.chat()` reintenta la petición HTTP hasta 3 veces con backoff
corto (1s, 2s, 4s) cuando la excepción es `httpx.TransportError` (cubre
`RemoteProtocolError`, `ConnectError`, `ReadTimeout` de conexión, etc.). Si
los 3 intentos fallan, la excepción se propaga como hoy. Este reintento es
independiente del reintento existente en `translate_block` (que es por
contenido de respuesta incompleto, no por fallo de conexión) y absorbe
blips cortos como el del log original sin necesitar abortar el job.

## Resume por bloque (`app/translator.py`)

`translate_srt_file` gana dos parámetros:

- `resume_blocks: dict[int, dict[int, str]] | None` — bloques ya exitosos de
  un intento anterior, `posición → {índice: texto}`.
- `on_block_result: Callable[[int, dict[int, str], bool], None] | None` —
  callback invocado tras cada intento de bloque (nuevo, no reusado de
  `resume_blocks`) con `(posición, traducciones, éxito)`.

Por cada posición del archivo: si está presente en `resume_blocks`, se
reutiliza directamente sin llamar a Ollama (se sigue invocando
`on_block_translated` para que la UI refleje progreso). Si no, se traduce
como hoy vía `translate_block` y se reporta el resultado por
`on_block_result` para que el caller lo persista.

El archivo de salida se sigue escribiendo una sola vez, al terminar de
procesar todos los bloques del archivo — sin cambios respecto al
comportamiento actual.

## Reintento automático y reanudación (`app/queue_worker.py`)

`process_job`, por cada `job_file` del job (en el orden actual):

- Si su `status` ya es `completed`/`completed_with_errors`, se salta por
  completo (no se vuelve a tocar).
- Si no, se cargan sus `resume_blocks` vía
  `get_completed_job_file_blocks` y se llama a `translate_srt_file` con
  ellos y con un `on_block_result` que hace upsert en `job_file_blocks`.
- Al terminar el archivo con éxito, se borran sus filas de
  `job_file_blocks` (`delete_job_file_blocks`).

Si `translate_srt_file` lanza una excepción (tras agotar los reintentos de
red de `OllamaClient`):

- Si `jobs.retry_count < 3`: se incrementa `retry_count` y el job vuelve a
  `status=pending` (sin tocar `job_files` ni `job_file_blocks` — el worker
  lo retomará solo en su próximo ciclo de polling, reanudando el archivo
  interrumpido desde el bloque donde quedó).
- Si `retry_count >= 3`: el job pasa a `status=failed` con el mensaje de
  error, igual que hoy, pero sigue siendo reanudable manualmente.

Nuevo endpoint `POST /api/jobs/{id}/resume`:

- Permitido solo si `status` es `failed` o `stopped`; en cualquier otro
  caso, 409.
- Resetea `retry_count=0` y `cancel_requested=0`, pone `status=pending`.
  El worker lo recoge normalmente y reanuda desde donde quedó (mismo
  mecanismo que el reintento automático).

## Cancelación manual (`app/queue_worker.py` + `app/routes/jobs.py`)

Nuevo endpoint `POST /api/jobs/{id}/stop`:

- Si `status=pending`: pasa directo a `status=stopped` (nunca llegó a
  correr, no hay nada que el worker deba notar).
- Si `status=processing`: pone `cancel_requested=1` y responde de
  inmediato (la cancelación es cooperativa, no instantánea).
- Cualquier otro estado: 409.

En `process_job`, entre cada bloque (vía el mismo punto donde se invoca
`on_block_translated`/`on_block_result`), el worker consulta
`get_job_cancel_requested`. Si está activo:

- Corta el procesamiento del archivo actual en el bloque en curso (no lo
  deja a medio bloque, el bloque en vuelo termina normal).
- No se toca el archivo de salida del `job_file` interrumpido (se escribe
  solo cuando un archivo termina completo, igual que en el flujo normal).
- Pone `status=stopped`, `cancel_requested=0`, y termina el job sin generar
  `output.zip`.

Un job `stopped` es reanudable con `/resume`, igual que uno `failed`.

## Descarga parcial (`app/routes/jobs.py`)

`GET /api/jobs/{id}/download` dejar de exigir
`status in (completed, completed_with_errors)` a nivel job:

- Si el job está `completed`/`completed_with_errors`: comportamiento
  actual sin cambios (sirve el `output.zip` ya armado).
- Si el job está en cualquier otro estado (`processing`, `failed`,
  `stopped`) y tiene al menos un `job_file` en `completed` o
  `completed_with_errors`: arma un zip al vuelo (usando
  `zip_utils.create_zip` sobre una carpeta temporal con solo esos
  archivos, o filtrando directamente qué archivos incluir) y lo sirve como
  `parcial_<nombre_original>.zip`.
- Si no hay ningún `job_file` completo todavía: 404 "El archivo de salida
  no existe".

Explícitamente fuera de alcance: incluir contenido del archivo que quedó a
medio traducir. Solo se descargan archivos `.srt` completos.

## Frontend (`static/app.js`)

En `renderJobRow`:

- Botón **"Detener"**: visible si `status` es `pending` o `processing`.
  Llama a `POST /api/jobs/{id}/stop` y refresca la lista.
- Botón **"Reanudar"**: visible si `status` es `failed` o `stopped`. Llama a
  `POST /api/jobs/{id}/resume` y refresca la lista.
- El link de descarga deja de condicionarse solo a
  `completed`/`completed_with_errors`: también aparece (con texto
  "Descargar parcial") cuando el job no terminó pero `processed_files > 0`
  (hay al menos un archivo completo dentro del job).
- Los estados nuevos (`stopped`) se muestran en la celda de status igual
  que los demás (texto plano, sin traducción especial por ahora).

## Testing

TDD por pieza, siguiendo el patrón existente de transportes falsos:

- `app/ollama_client.py`: transporte falso que falla N veces antes de
  responder 200 — verifica que `chat()` reintenta y eventualmente
  devuelve, y que agota los 3 intentos y propaga si nunca responde.
- `app/translator.py`: `translate_srt_file` con `resume_blocks` no vacío —
  verifica que no se llama a Ollama para las posiciones ya resueltas y que
  el resultado final combina lo reanudado con lo nuevo.
- `app/db.py`: upsert/lectura/borrado de `job_file_blocks`,
  `retry_count`/`cancel_requested`, y que `reset_stale_processing_jobs` ya
  no resetea contadores de progreso.
- `app/queue_worker.py` (integración): simular una excepción a mitad de un
  archivo (segundo bloque) con un cliente falso, verificar que el job
  vuelve a `pending` con `retry_count=1`, y que un segundo `process_job`
  sobre el mismo job no vuelve a llamar al cliente para el primer bloque.
  Simular también cancelación (`cancel_requested`) y verificar que el job
  termina en `stopped` sin tocar el resto del archivo.
- `app/routes/jobs.py`: `/stop`, `/resume` y `/download` en sus distintos
  estados (incluida la descarga parcial con archivos completos mezclados
  con uno pendiente).
