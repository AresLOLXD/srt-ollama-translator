# Diseño: Traductor de subtítulos SRT con Ollama local

**Fecha:** 2026-09-13
**Repositorio GitHub:** `srt-ollama-translator` (privado)

## Objetivo

Aplicación web personal (un solo usuario, ejecutándose en la máquina local) que permite:

1. Subir un archivo `.zip` con uno o más archivos `.srt` en cualquier idioma origen.
2. Seleccionar el modelo de Ollama local a usar para la traducción.
3. Traducir todos los `.srt` al español, preservando la numeración y los timestamps.
4. Encolar varios trabajos (zips) y procesarlos en orden, con progreso visible en tiempo real.
5. Descargar un `.zip` con los `.srt` traducidos al finalizar cada trabajo.

## Alcance y restricciones

- Un solo usuario, sin autenticación.
- Corre en la máquina local del usuario, empaquetado en una imagen de contenedor (Docker/Podman compatible).
- Ollama corre en el **host**, no dentro del contenedor (requiere acceso a GPU). La app se conecta a él vía una URL configurable.
- Sin dependencias de servicios externos en la nube.

## Arquitectura

- **Backend:** Python + FastAPI, gestionado con **uv** (`pyproject.toml` + `uv.lock`).
- **Frontend:** página estática servida por FastAPI (HTML + JS vanilla, sin build step ni framework).
- **Persistencia:** SQLite (tablas `jobs`, `job_files`, `config`).
- **Cola de trabajos:** persistente en SQLite; un worker en background (asyncio task) procesa los jobs `pending` de uno en uno, en orden de llegada. Si el proceso se reinicia, retoma el job que quedó `processing` o los `pending` restantes.
- **Contenerización:** imagen Docker (compatible con Podman) construida con `uv`, con volúmenes para persistir la base SQLite y los archivos de trabajo fuera del contenedor.

## Componentes

```
app/
  main.py          # app FastAPI, monta rutas y estáticos
  routes/
    jobs.py        # endpoints de jobs (crear, listar, detalle, descarga, borrar)
    models.py      # GET /api/models (proxy a Ollama /api/tags)
    config.py      # GET/POST /api/config (URL de Ollama)
  queue_worker.py  # bucle asyncio que procesa jobs pendientes secuencialmente
  translator.py    # parseo SRT -> bloques -> traducción -> reconstrucción SRT
  ollama_client.py # wrapper HTTP a la API de Ollama (/api/tags, /api/chat)
  db.py            # acceso a SQLite (creación de esquema, queries)
  storage/         # (en runtime, montado como volumen) zips y srt por job
static/
  index.html
  app.js
  styles.css
pyproject.toml
uv.lock
Dockerfile
docker-compose.yml
```

## Modelo de datos (SQLite)

**`jobs`**
| campo | tipo | notas |
|---|---|---|
| id | TEXT (uuid) | PK |
| original_zip_name | TEXT | |
| model | TEXT | modelo de Ollama usado |
| source_lang | TEXT | "auto" u código de idioma |
| status | TEXT | `pending` / `processing` / `completed` / `completed_with_errors` / `failed` |
| total_files | INTEGER | |
| processed_files | INTEGER | |
| error_message | TEXT | nullable |
| created_at | TEXT | ISO 8601 |
| updated_at | TEXT | ISO 8601 |

**`job_files`**
| campo | tipo | notas |
|---|---|---|
| id | TEXT (uuid) | PK |
| job_id | TEXT | FK a `jobs.id` |
| filename | TEXT | ruta relativa dentro del zip |
| status | TEXT | `pending` / `processing` / `completed` / `completed_with_errors` |
| total_blocks | INTEGER | |
| translated_blocks | INTEGER | |
| failed_blocks | INTEGER | bloques que no se pudieron traducir tras reintentos |

**`config`**
| campo | tipo | notas |
|---|---|---|
| key | TEXT | PK, ej. `ollama_base_url` |
| value | TEXT | |

## Flujo de traducción

1. **Subida (`POST /api/jobs`)**: se recibe el zip, se extrae a `storage/{job_id}/input/`, se listan recursivamente todos los `.srt`. Se crea el registro `jobs` (`status=pending`) y un `job_files` por cada srt encontrado.

2. **Procesamiento (worker, un job a la vez)**:
   - Marca el job como `processing`.
   - Por cada `job_file`:
     - Parsea el `.srt` con la librería `srt` (lista de índice, tiempos, texto).
     - Agrupa los subtítulos en bloques de ~20-30 líneas (configurable), sin exceder un límite razonable de tokens por bloque.
     - Para cada bloque, arma un prompt que:
       - Instruye traducir al español conservando el mismo número de líneas.
       - Envía cada línea prefijada con su índice `[N]` y pide que la respuesta preserve exactamente esos índices, uno por línea.
     - Llama a Ollama (`/api/chat`) con el modelo seleccionado.
     - Parsea la respuesta extrayendo pares `[N] -> texto traducido`.
     - **Validación**: si faltan índices esperados en la respuesta, reintenta (máx. 3 intentos, con prompt reforzado). Si tras 3 intentos siguen faltando líneas, esas líneas quedan en el idioma original y se cuentan en `failed_blocks`.
     - Actualiza `job_files.translated_blocks` después de cada bloque (para progreso en tiempo real).
   - Reconstruye el `.srt` traducido (mismos tiempos, texto traducido o el original si falló) en `storage/{job_id}/output/`.
   - Al terminar todos los archivos: comprime `output/` en un zip final. Si algún archivo tuvo `failed_blocks > 0`, el job queda `completed_with_errors`; si no, `completed`. Si hay un error no recuperable (ej. Ollama inalcanzable), el job queda `failed` con `error_message`.

3. **Descarga (`GET /api/jobs/{id}/download`)**: sirve el zip final generado.

## Endpoints API

- `GET /api/models` — lista modelos disponibles desde Ollama.
- `GET /api/config` / `POST /api/config` — leer/actualizar `ollama_base_url` (persistido en SQLite, con default desde la env var `OLLAMA_BASE_URL`).
- `POST /api/jobs` — sube zip + `model` + `source_lang`, crea y encola el job.
- `GET /api/jobs` — lista todos los jobs con estado/progreso resumido.
- `GET /api/jobs/{id}` — detalle del job, incluyendo estado por archivo.
- `GET /api/jobs/{id}/download` — descarga el zip resultante.
- `DELETE /api/jobs/{id}` — elimina el job y sus archivos en disco.

## Frontend

Página única sin framework:
- Configuración: campo para `ollama_base_url` (con botón guardar) y selector de modelo (botón "refrescar" que vuelve a llamar `/api/models`).
- Formulario de subida: input de archivo (zip), selector de idioma origen ("auto" + opciones comunes), botón "Encolar trabajo".
- Tabla de trabajos: nombre del zip, estado, progreso (archivos procesados / total, bloques traducidos / total del archivo actual), botón de descarga habilitado cuando el estado es `completed` o `completed_with_errors`.
- Polling cada 3 segundos a `GET /api/jobs` mientras haya jobs no terminados.

## Contenerización

- **Dockerfile**: imagen base `python:3.12-slim`, instala `uv`, corre `uv sync --frozen`, copia `app/` y `static/`, expone el puerto configurable (`PORT`, default 8000), arranca con `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- **Volúmenes**:
  - `/data/db` → base SQLite.
  - `/data/storage` → zips de entrada/salida y srt intermedios.
- **Variables de entorno**:
  - `OLLAMA_BASE_URL` (default `http://host.containers.internal:11434`, ajustable también desde la UI).
  - `PORT` (default `8000`).
- **`docker-compose.yml`**: compatible con `podman-compose` y `docker compose`, define el servicio, mapea puerto y monta los volúmenes. Ollama no se incluye en el compose (se asume que corre en el host); el README documenta cómo ajustar `OLLAMA_BASE_URL` si se quisiera correr Ollama en otro contenedor.

## Manejo de errores

- Bloque de traducción sin todos los índices esperados tras 3 reintentos → las líneas faltantes quedan en el idioma original, se cuentan en `failed_blocks`, el job termina como `completed_with_errors`.
- Ollama inalcanzable (URL mal configurada, servicio caído) → el job en curso pasa a `failed` con `error_message` descriptivo; el worker continúa con el siguiente job pendiente.
- Zip sin archivos `.srt` → el job se rechaza inmediatamente en `POST /api/jobs` con un error 400.

## Fuera de alcance (explícitamente)

- Autenticación / multiusuario.
- Traducción a idiomas distintos de español.
- Edición manual de las traducciones desde la UI.
- Reintento automático de jobs `failed` (se puede volver a subir el zip manualmente).
