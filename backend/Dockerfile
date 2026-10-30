FROM python:3.12-slim

WORKDIR /app

# Native PostgreSQL17 tools for private QA backups and independent restores.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-harness.txt ./
# The same six-source QA image can host a separate restricted Harness cron.
# SDK import alone cannot start a provider call; runtime permission gates stay off.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-harness.txt

COPY backend/app ./app
COPY backend/config/china-calendar-2026.json ./config/china-calendar-2026.json
COPY backend/harness ./harness
COPY backend/sql ./sql
COPY backend/migrate.py ./migrate.py

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
