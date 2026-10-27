# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS python
WORKDIR /build
COPY backend/requirements.txt backend/requirements-harness.txt ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.txt -r requirements-harness.txt
FROM postgres:17-bookworm
COPY --from=python /usr/local /usr/local
WORKDIR /app
COPY --chown=postgres:postgres backend/app ./app
COPY --chown=postgres:postgres backend/sql ./sql
COPY --chown=postgres:postgres backend/harness ./harness
COPY --chown=postgres:postgres backend/config ./config
COPY --chown=postgres:postgres backend/migrate.py ./migrate.py
ENV QUIZ_AUTO_ONLY=1 AI_AUTOMATION_ENABLED=0 QUIZ_DAILY_PREP_ENABLED=0 PYTHONDONTWRITEBYTECODE=1 AI_QUEUE_AUTOPLAN=0 AI_FULL_CORPUS_ENABLED=0 QUIZ_PRIVATE_BACKUP_ENABLED=0
USER postgres
ENTRYPOINT []
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
