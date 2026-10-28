FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.23 && uv sync --frozen --no-dev --no-install-project --no-cache
COPY backend ./backend
COPY alembic.ini ./
COPY loadtests ./loadtests
COPY scripts ./scripts
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/backend/src" PYTHONUNBUFFERED=1
CMD ["uvicorn", "zenkai.api:app", "--host", "0.0.0.0", "--port", "8000"]
