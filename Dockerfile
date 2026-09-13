FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /srv/app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY static/ ./static/

ENV DB_PATH=/data/db/jobs.db
ENV STORAGE_DIR=/data/storage
ENV OLLAMA_BASE_URL=http://host.containers.internal:11434
ENV PORT=8000

VOLUME ["/data/db", "/data/storage"]
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
