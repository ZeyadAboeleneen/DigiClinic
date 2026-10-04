# DigiClinic web + scheduler image (same image, different command — see compose.yml).
# Build context is the repo root:  docker compose -f deploy/compose.yml build
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    PATH=/app/.venv/bin:$PATH

# PostgreSQL 16 client (pg_dump/pg_restore must match the server's major version for backups).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Chromium for prescription PDFs (05) — with its system libraries and fonts.
RUN playwright install --with-deps chromium

COPY . .
RUN SECRET_KEY=build-only FIELD_ENCRYPTION_KEY=build-only DATABASE_URL=sqlite:////tmp/build.db \
    python manage.py collectstatic --noinput

# Unprivileged user; /data holds private media + backups (a named volume, created with this ownership).
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/media /data/backups \
 && chown -R app:app /data /app/staticfiles
USER app

EXPOSE 8000
# gthread: few processes (each keeps one warm Chromium for PDFs), several threads each.
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", \
     "--worker-class", "gthread", "--timeout", "90", "--access-logfile", "-"]
