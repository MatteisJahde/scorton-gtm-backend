#!/bin/bash
cd "$(dirname "$0")"
lsof -ti :8012 | xargs kill -9 2>/dev/null || true
sleep 1
exec python3 serve_export.py
