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

### Reinicio automático del contenedor con Podman

`docker-compose.yml` ya define `restart: unless-stopped`, pero a diferencia de
Docker, Podman no tiene un daemon en segundo plano que aplique esa política
por sí solo tras un reinicio del host. Para que el contenedor se reinicie
solo (por ejemplo, si Ollama tarda en levantar y el contenedor falla al
arrancar, o tras reiniciar la máquina), hay que habilitar el servicio de
systemd que Podman provee para esto:

```bash
# Habilita el servicio que reinicia, al boot, los contenedores con política de restart
systemctl --user enable --now podman-restart.service

# Si usas Podman en modo rootless, además necesitas "lingering" para que tus
# servicios de usuario sigan activos sin que haya una sesión iniciada
loginctl enable-linger "$USER"
```

Con esto, cualquier contenedor creado con `restart: unless-stopped` (o
`always`) se reiniciará automáticamente si se cae o si la máquina se reinicia,
igual que ocurriría con Docker.

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
