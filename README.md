# srt-ollama-translator

Aplicación web local para traducir archivos `.srt` (subtítulos) al español
usando un modelo de [Ollama](https://ollama.com) corriendo en tu máquina.

## Requisitos

- Ollama corriendo localmente con al menos un modelo descargado
  (`ollama pull llama3.1`, por ejemplo).
- Docker o Podman (con `docker-compose`/`podman-compose`).

## Uso con Podman

```bash
podman-compose up --build
```

## Uso con Docker

```bash
docker compose up --build
```

Luego abre `http://localhost:8000`.

## Configurar la URL de Ollama

Por defecto la app espera a Ollama en `http://host.containers.internal:11434`
(la forma estándar de referirse al host desde un contenedor en Podman/Docker
Desktop). Si tu configuración es distinta:

- Cambia la variable de entorno `OLLAMA_BASE_URL` en `docker-compose.yml`, o
- Ajusta la URL directamente desde la sección "Configuración" de la interfaz
  web una vez la app está corriendo (se guarda en la base de datos y persiste
  entre reinicios).

## Desarrollo local (sin contenedor)

```bash
uv sync
export DB_PATH=./dev-data/jobs.db
export STORAGE_DIR=./dev-data/storage
uv run uvicorn app.main:app --reload
```

## Tests

```bash
uv run pytest -v
```
