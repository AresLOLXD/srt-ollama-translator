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

## Configurar puerto y URL de Ollama

Copia `.env.example` a `.env` y ajusta las variables antes de ejecutar:

```bash
cp .env.example .env
# Edita .env para cambiar HOST_PORT y/o OLLAMA_BASE_URL según sea necesario
```

- `HOST_PORT`: Puerto en el host donde se expone la app (por defecto `8000`).
- `OLLAMA_BASE_URL`: URL base de Ollama (por defecto `http://host.containers.internal:11434`).

Alternativamente, puedes editar `docker-compose.yml` directamente o ajustar la URL desde la sección "Configuración" de la interfaz web una vez la app está corriendo (se guarda en la base de datos y persiste entre reinicios).

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
