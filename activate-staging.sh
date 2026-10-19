#!/usr/bin/env bash
set -Eeuo pipefail

exec 9>"$HOME/.hms-deploy.lock"
flock -n 9 || { echo "DEPLOY_BUSY"; exit 1; }

TARGET="localhost/hms-api:${1:?Commit required}"
[[ "${1:-}" =~ ^[a-f0-9]{40}$ ]] || { echo INVALID_COMMIT; exit 1; }
podman image exists "$TARGET"

OLD=$(podman inspect hms-api-staging --format '{{.Image}}')
podman tag "$OLD" localhost/hms-api:rollback-manual

rollback() {
  trap - ERR
  podman tag localhost/hms-api:rollback-manual localhost/hms-api:dev
  sudo -n systemctl --user --machine=student@.host restart hms-api-staging.service || true
  echo "DEPLOY_FAILED_ROLLBACK_ATTEMPTED" >&2
  exit 1
}
trap rollback ERR

podman tag "$TARGET" localhost/hms-api:dev
sudo -n systemctl --user --machine=student@.host restart hms-api-staging.service

TARGET_ID=$(podman image inspect "$TARGET" --format '{{.Id}}')

for i in $(seq 1 15); do
  RUNNING=$(podman inspect hms-api-staging --format '{{.Image}}' 2>/dev/null || true)
  if [ "$RUNNING" = "$TARGET_ID" ] && curl -fsS http://127.0.0.1:8003/openapi.json >/dev/null 2>&1; then
    trap - ERR
    echo "DEPLOY_SUCCESS"
    exit 0
  fi
  sleep 1
done

false
