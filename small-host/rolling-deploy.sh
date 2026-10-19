#!/usr/bin/env bash
#
# Replace the game container one JVM at a time.
#
#   1. pull the image; stop if the running container already uses it. When no
#      container runs, skip to step 4, which is how the first start happens.
#   2. POST the drain endpoint, so the server stops taking new matches
#   3. wait for the container to exit by itself once its last match ends
#   4. docker compose up -d, which starts the new image
#   5. wait for health to report UP
#
# Run by game-deploy.timer. flock keeps a tick from starting while the previous
# one still waits for matches.

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
set -a; . ./.env; set +a

NAME="${GAME_NAME:-dev-game}"
IMAGE="${GAME_IMAGE:-ghcr.io/apptive-game-team/ac-game:dev}"
PUBLIC_PORT="${PUBLIC_PORT:-7080}"
MANAGEMENT_PORT="${MANAGEMENT_PORT:-7081}"
CICD_TOKEN="${CICD_TOKEN:?CICD_TOKEN is required in .env}"
DRAIN_PATH="${DRAIN_PATH:-/api/server/servers/mine/state/draining}"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-3600}"   # seconds to wait for matches to end
STOP_GRACE="${STOP_GRACE:-30}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"
DISCORD_WEBHOOK_URL="${DEPLOY_DISCORD_WEBHOOK_URL:-}"

exec 9>/tmp/game-rolling-deploy.lock
flock -n 9 || exit 0

log() { printf '%s [deploy] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

notify() {
    log "$*"
    [ -n "$DISCORD_WEBHOOK_URL" ] || return 0
    curl -sf -X POST -H 'Content-Type: application/json' \
        --data "$(printf '{"content":"[game deploy] %s"}' "$*")" \
        "$DISCORD_WEBHOOK_URL" >/dev/null || true
}

is_running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ]; }

docker pull -q "$IMAGE" >/dev/null
new_id="$(docker image inspect -f '{{.Id}}' "$IMAGE")"

if is_running; then
    [ "$(docker inspect -f '{{.Image}}' "$NAME")" != "$new_id" ] || exit 0

    notify "new image; draining ${NAME}, waiting up to $((DRAIN_TIMEOUT / 60))m for matches to end"
    drained=0
    for attempt in 1 2 3; do
        if curl -sf --max-time 10 -X POST -H "Authorization: Bearer ${CICD_TOKEN}" \
            "http://127.0.0.1:${PUBLIC_PORT}${DRAIN_PATH}" >/dev/null; then
            drained=1
            break
        fi
        log "drain request failed (attempt ${attempt}/3)"
        sleep 5
    done

    if [ "$drained" -eq 1 ] && timeout "$DRAIN_TIMEOUT" docker wait "$NAME" >/dev/null 2>&1; then
        log "${NAME} exited on its own"
    else
        notify "${NAME} could not be drained or still had sessions after $((DRAIN_TIMEOUT / 60))m; stopping it"
        docker stop -t "$STOP_GRACE" "$NAME" >/dev/null || true
    fi
fi

notify "starting ${NAME} on ${IMAGE}"
docker compose up -d --force-recreate game >/dev/null

deadline=$((SECONDS + HEALTH_TIMEOUT))
until curl -sf --max-time 5 "http://127.0.0.1:${MANAGEMENT_PORT}/actuator/health" | grep -qF '"status":"UP"'; do
    if ! is_running || [ "$SECONDS" -gt "$deadline" ]; then
        notify "${NAME} did not become healthy. Last logs:"
        docker logs --tail 50 "$NAME" 2>&1 || true
        exit 1
    fi
    sleep 3
done
notify "${NAME} is live"
