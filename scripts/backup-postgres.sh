#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

exec 9>"$ROOT_DIR/.backup.lock"
if ! flock -n 9; then
    echo "Another database backup is already running."
    exit 0
fi

if [[ ! "$BACKUP_RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]]; then
    echo "BACKUP_RETENTION_DAYS must be a positive integer." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
backup_name="musicbot-$(date -u +%Y%m%dT%H%M%SZ).dump"
temporary_file="$(mktemp "$BACKUP_DIR/.musicbot-backup.XXXXXX")"
trap 'rm -f "$temporary_file"' EXIT

docker compose -f "$ROOT_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U musicbot -d musicbot --format=custom > "$temporary_file"
if [[ ! -s "$temporary_file" ]]; then
    echo "PostgreSQL backup is empty; refusing to publish it." >&2
    exit 1
fi

chmod 600 "$temporary_file"
mv -- "$temporary_file" "$BACKUP_DIR/$backup_name"
trap - EXIT
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'musicbot-*.dump' \
    -mtime "+$BACKUP_RETENTION_DAYS" -delete
echo "Created PostgreSQL backup: $BACKUP_DIR/$backup_name"
