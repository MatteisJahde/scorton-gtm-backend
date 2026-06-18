#!/usr/bin/env bash
set -euo pipefail

PORT=8000
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Stopping anything on port ${PORT}..."
PIDS=$(lsof -ti:"${PORT}" 2>/dev/null || true)
if [ -n "${PIDS}" ]; then
  kill -9 ${PIDS}
  echo "Killed: ${PIDS}"
else
  echo "Port ${PORT} was already free."
fi

cd "${PROJECT_DIR}"
echo "Starting FastAPI from ${PROJECT_DIR}..."
exec python3 -m uvicorn main:app --reload --host 127.0.0.1 --port "${PORT}"
