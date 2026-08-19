#!/usr/bin/env bash
# Roll the hermes container onto a new image, with health check and rollback.
# Usage: hermes-deploy.sh <image-ref>
set -euo pipefail

IMAGE="${1:?usage: hermes-deploy.sh <image-ref>}"
NAME=hermes
DATA=/opt/hermes-data
ENVFILE=/etc/hermes/deploy.env
# Images of the deployed repo to keep: the live one plus this many older ones,
# so a bad release can be rolled back by hand without pulling 4 GB again.
KEEP="${KEEP:-2}"

log() { echo "[hermes-deploy] $*"; }

PREV="$(docker inspect "$NAME" --format '{{.Config.Image}}' 2>/dev/null || true)"
log "current image: ${PREV:-<none>}"
log "target image:  $IMAGE"

log "pulling..."
docker pull "$IMAGE"

start_container() {
  local img="$1"
  # Stop gracefully before removing. `docker rm -f` alone SIGKILLs, so the
  # gateway never runs its shutdown path and its lifecycle ledger records the
  # release as an unclean exit indistinguishable from an OOM or VM death.
  docker stop --timeout 30 "$NAME" >/dev/null 2>&1 || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" --restart unless-stopped \
    -v "$DATA":/opt/data \
    -p 127.0.0.1:9119:9119 \
    -e HERMES_UID=10000 -e HERMES_GID=10000 \
    -e HERMES_DASHBOARD=1 \
    -e HERMES_DASHBOARD_HOST=0.0.0.0 \
    -e HERMES_DASHBOARD_PORT=9119 \
    --env-file "$ENVFILE" \
    "$img" gateway run >/dev/null
}

healthy() {
  for i in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:9119/login || true)"
    if [ "$code" = "200" ]; then log "healthy after ${i}0s (HTTP $code)"; return 0; fi
    sleep 10
  done
  return 1
}

# Drop old builds of the deployed repo. Only that repo is touched: images from
# other repos are left alone rather than guessed at. docker images lists newest
# first, so everything past $KEEP is stale. The live image is filtered out
# explicitly in case the ordering ever disagrees with what is running.
cleanup_images() {
  local repo="${IMAGE%:*}"
  local live stale freed_before freed_after
  live="$(docker inspect "$NAME" --format '{{.Image}}' 2>/dev/null || true)"

  stale="$(docker images "$repo" --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}}' \
           | awk -v k="$KEEP" 'NR>k {print $1"\t"$2}' \
           | grep -v "^${live}\b" || true)"

  freed_before="$(docker system df --format '{{.Reclaimable}}' 2>/dev/null | head -1 || true)"

  if [ -n "$stale" ]; then
    log "removing $(wc -l <<<"$stale") stale image(s) of $repo (keeping $KEEP):"
    while IFS=$'\t' read -r id tag; do
      [ -z "$id" ] && continue
      log "  rmi $tag"
      docker rmi "$tag" >/dev/null 2>&1 || docker rmi -f "$id" >/dev/null 2>&1 || log "  (could not remove $tag)"
    done <<<"$stale"
  else
    log "no stale $repo images to remove"
  fi

  # Untagged layers orphaned by the rmi calls above.
  docker image prune -f >/dev/null 2>&1 || true
  freed_after="$(docker system df --format '{{.Reclaimable}}' 2>/dev/null | head -1 || true)"
  log "reclaimable: ${freed_before:-?} -> ${freed_after:-?}   disk free: $(df -h / | awk 'NR==2{print $4}')"
}

# Swapping the container kills whatever turn the agent is mid-way through, and
# the user sees "Gateway shutting down - Your current task will be interrupted".
# An idle gateway logs nothing at all, so silence is a reliable idle signal.
# This waits for that silence, but never blocks a release indefinitely.
wait_for_idle() {
  local quiet="${IDLE_QUIET:-45}" max="${IDLE_MAX_WAIT:-600}" waited=0 lines
  docker inspect "$NAME" >/dev/null 2>&1 || { log "no running container; nothing to wait for"; return 0; }
  log "waiting for the agent to go idle (quiet=${quiet}s, give up after ${max}s)..."
  while [ "$waited" -lt "$max" ]; do
    lines="$(docker logs --since "${quiet}s" "$NAME" 2>&1 | grep -c . || true)"
    if [ "$lines" -eq 0 ]; then
      log "agent idle (silent for ${quiet}s; waited ${waited}s)"
      return 0
    fi
    log "  busy: ${lines} log line(s) in the last ${quiet}s (waited ${waited}s)"
    sleep 15; waited=$((waited + 15))
  done
  log "still busy after ${max}s - proceeding; the graceful stop below gives the agent its shutdown path"
  return 0
}

wait_for_idle

log "starting new container..."
start_container "$IMAGE"

if healthy; then
  log "deploy OK -> $IMAGE"
  cleanup_images
  exit 0
fi

log "HEALTH CHECK FAILED - last 40 log lines:"
docker logs --tail 40 "$NAME" 2>&1 || true

if [ -n "$PREV" ] && [ "$PREV" != "$IMAGE" ]; then
  log "rolling back to $PREV"
  start_container "$PREV"
  if healthy; then log "rollback OK - service restored on $PREV"; else log "ROLLBACK ALSO UNHEALTHY - manual action needed"; fi
fi
# Never prune on a failed deploy: the previous image is the way back.
exit 1
