FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml ./
COPY coletor ./coletor
RUN pip install . \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home coletor

COPY alembic.ini ./
COPY alembic ./alembic

USER coletor

CMD ["sh", "-c", "alembic upgrade head && coletor agendar --agora"]
