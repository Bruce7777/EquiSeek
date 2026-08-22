FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN python -m venv /opt/venv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]" pytest==9.0.3
COPY alembic.ini ./
COPY migrations ./migrations
COPY examples ./examples
RUN mkdir -p /data/artifacts /data/workspaces && chown -R 65532:65532 /data /app
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "aegisrun.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
