#!/bin/sh
# InfraEngine starten op http://127.0.0.1:8000
cd "$(dirname "$0")"
exec ./.venv/bin/python -m uvicorn main:app --app-dir backend --port 8000
