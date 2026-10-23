#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! docker inspect musicbot >/dev/null 2>&1; then
    exit 0
fi

health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' musicbot)"
if [[ "$health_status" == "unhealthy" ]]; then
    echo "musicbot is unhealthy; restarting the container."
    docker compose restart musicbot
fi
