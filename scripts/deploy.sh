#!/usr/bin/env bash
# Run on the dedicated demo host after placing its secrets in .env.
set -euo pipefail
cd "$(dirname "$0")/.."
dc=(docker compose -f compose.yaml -f compose.deploy.yaml)
python3 scripts/init_local.py
"${dc[@]}" config --quiet
"${dc[@]}" build api console
"${dc[@]}" --profile load pull load
"${dc[@]}" up -d postgres
"${dc[@]}" run --rm --no-deps api alembic upgrade head
"${dc[@]}" up -d
for attempt in {1..45}; do
    if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
        printf '%s\n' 'Control API is ready. Check public HTTPS after DNS points to this host.'
        "${dc[@]}" ps
        exit 0
    fi
    sleep 2
done
printf '%s\n' 'API did not become ready. Inspect docker compose logs.' >&2
exit 1
