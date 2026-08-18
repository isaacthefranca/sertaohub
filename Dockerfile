FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-production.txt ./
RUN pip install -r requirements-production.txt
COPY . .
RUN chmod +x scripts/start.sh scripts/backup.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1
CMD ["./scripts/start.sh"]
