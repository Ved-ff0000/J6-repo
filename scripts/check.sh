#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
(cd frontend && npm run format:check && npm run typecheck && npm test && npm run build)
(cd backend && python -m ruff check . && python -m ruff format --check . && python -m mypy app && python -m pytest -q)
