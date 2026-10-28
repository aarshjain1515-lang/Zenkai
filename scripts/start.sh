#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/init_local.py
uv sync --frozen --python 3.12
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
docker compose config --quiet
docker compose build api console
docker compose --profile load pull load
docker compose up -d
printf '%s\n' 'Zenkai: http://localhost:5173' 'Separate test target: http://localhost:8080/campus' 'Grafana: http://localhost:3300'
