FROM python:3.12.15-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend ./backend
COPY alembic.ini ./
COPY loadtests ./loadtests
RUN pip install --no-cache-dir uv==0.9.5 && uv sync --frozen --no-dev --no-cache
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "zenkai.api:app", "--host", "0.0.0.0", "--port", "8000"]
