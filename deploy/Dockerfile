FROM python:3.12-slim AS runtime
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.7 && uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY configs ./configs
RUN useradd --uid 10001 --create-home dbic
USER dbic
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["uvicorn", "dbic.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8086"]

FROM runtime AS verification
USER root
RUN uv sync --frozen
COPY tests ./tests
COPY scripts ./scripts
USER dbic
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
