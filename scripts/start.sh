#!/bin/sh
set -eu
python migrate.py
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
