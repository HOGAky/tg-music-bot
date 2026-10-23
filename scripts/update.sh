#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOYED_FILE="$ROOT_DIR/.deploy-current"
FAILED_FILE="$ROOT_DIR/.deploy-failed"
DEPLOY_ENV_FILE="$ROOT_DIR/.deploy-env"
APP_IMAGE_NAME="tg-music-bot"
NGINX_IMAGE_NAME="tg-music-bot-nginx"

cd "$ROOT_DIR"
exec 9>"$ROOT_DIR/.deploy.lock"
if ! flock -n 9; then
    echo "Another deployment check is already running."
    exit 0
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Deployment stopped: tracked local changes must be reviewed first." >&2
    exit 1
fi

git fetch --quiet origin "$BRANCH:refs/remotes/origin/$BRANCH"
TARGET_REV="$(git rev-parse "refs/remotes/origin/$BRANCH")"
if [[ -f "$FAILED_FILE" ]] && [[ "$(<"$FAILED_FILE")" == "$TARGET_REV" ]]; then
    echo "Deployment of $TARGET_REV previously failed; waiting for a new commit."
    exit 0
fi
if [[ -f "$DEPLOYED_FILE" ]] && [[ "$( <"$DEPLOYED_FILE" )" == "$TARGET_REV" ]]; then
    exit 0
fi

PREVIOUS_REV="$(git rev-parse HEAD)"
if [[ -f "$DEPLOYED_FILE" ]]; then
    PREVIOUS_REV="$(<"$DEPLOYED_FILE")"
fi
PREVIOUS_IMAGE_TAG="$PREVIOUS_REV"
if [[ "$PREVIOUS_IMAGE_TAG" == "$TARGET_REV" ]]; then
    PREVIOUS_IMAGE_TAG="rollback-$(date -u +%Y%m%dT%H%M%SZ)"
fi

for service in musicbot nginx; do
    if [[ "$service" == "musicbot" ]]; then
        container="musicbot"
        image_name="$APP_IMAGE_NAME"
    else
        container="musicbot-nginx"
        image_name="$NGINX_IMAGE_NAME"
    fi
    if docker inspect "$container" >/dev/null 2>&1; then
        running_image="$(docker inspect --format '{{.Image}}' "$container")"
        docker image tag "$running_image" "$image_name:$PREVIOUS_IMAGE_TAG"
    fi
done

git merge --ff-only "$TARGET_REV"
export MUSICBOT_IMAGE_TAG="$TARGET_REV"
docker compose build musicbot nginx
docker compose up -d --no-build --remove-orphans

wait_for_health() {
    local attempt service status all_healthy
    for attempt in $(seq 1 24); do
        all_healthy=true
        for service in musicbot musicbot-postgres musicbot-redis musicbot-nginx; do
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$service" 2>/dev/null || true)"
            if [[ "$status" == "unhealthy" ]]; then
                return 1
            fi
            if [[ "$status" != "healthy" ]]; then
                all_healthy=false
            fi
        done
        if [[ "$all_healthy" == "true" ]]; then
            return 0
        fi
        sleep 5
    done
    return 1
}

write_deploy_env() {
    local image_tag temporary_file
    image_tag="$1"
    temporary_file="$(mktemp "$ROOT_DIR/.deploy-env.XXXXXX")"
    printf 'MUSICBOT_IMAGE_TAG=%s\n' "$image_tag" > "$temporary_file"
    chmod 600 "$temporary_file"
    mv -- "$temporary_file" "$DEPLOY_ENV_FILE"
}

if wait_for_health; then
    printf '%s\n' "$TARGET_REV" > "$DEPLOYED_FILE"
    write_deploy_env "$TARGET_REV"
    rm -f "$FAILED_FILE"
    for image_name in "$APP_IMAGE_NAME" "$NGINX_IMAGE_NAME"; do
        if [[ "$PREVIOUS_IMAGE_TAG" != "$PREVIOUS_REV" ]]; then
            docker image rm "$image_name:$PREVIOUS_IMAGE_TAG" >/dev/null 2>&1 || true
        fi
        while IFS= read -r image_tag; do
            if [[ "$image_tag" == rollback-* ]]; then
                docker image rm "$image_name:$image_tag" >/dev/null 2>&1 || true
                continue
            fi
            [[ "$image_tag" =~ ^[0-9a-f]{40}$ ]] || continue
            [[ "$image_tag" == "$TARGET_REV" || "$image_tag" == "$PREVIOUS_REV" ]] && continue
            docker image rm "$image_name:$image_tag" >/dev/null 2>&1 || true
        done < <(docker image ls "$image_name" --format '{{.Tag}}')
    done
    docker image prune -f >/dev/null
    echo "Deployment succeeded: $TARGET_REV"
    exit 0
fi

echo "Deployment failed health checks: $TARGET_REV" >&2
if docker image inspect "$APP_IMAGE_NAME:$PREVIOUS_IMAGE_TAG" >/dev/null 2>&1 \
    && docker image inspect "$NGINX_IMAGE_NAME:$PREVIOUS_IMAGE_TAG" >/dev/null 2>&1; then
    export MUSICBOT_IMAGE_TAG="$PREVIOUS_IMAGE_TAG"
    docker compose up -d --no-build --force-recreate musicbot nginx
    if wait_for_health; then
        printf '%s\n' "$PREVIOUS_REV" > "$DEPLOYED_FILE"
        write_deploy_env "$PREVIOUS_IMAGE_TAG"
        echo "Rolled back to $PREVIOUS_REV" >&2
    else
        echo "Rollback image is unhealthy; manual recovery is required." >&2
    fi
else
    echo "No previous image is available for rollback." >&2
fi
printf '%s\n' "$TARGET_REV" > "$FAILED_FILE"
exit 1
