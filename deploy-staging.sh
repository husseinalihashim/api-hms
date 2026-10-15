#!/usr/bin/env bash
set -euo pipefail

cd /home/student/api-hms-source

OLD=$(podman image inspect localhost/hms-api:dev --format '{{.Id}}')

echo "Building HMS Backend..."
podman build -t localhost/hms-api:candidate -f Containerfile .

echo "Testing database..."
podman run --rm --network host --env-file .env localhost/hms-api:candidate python -c 'from app.database import engine; from sqlalchemy import text; c=engine.connect(); assert c.execute(text("SELECT 1")).scalar()==1; c.close(); print("Database OK")'

podman tag "$OLD" localhost/hms-api:rollback
podman tag localhost/hms-api:candidate localhost/hms-api:dev

echo "Restarting staging..."
if systemctl --user restart hms-api-staging.service; then
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS http://127.0.0.1:8003/openapi.json >/dev/null; then
      echo "STAGING DEPLOY SUCCESS"
      exit 0
    fi
    sleep 1
  done
fi

echo "Deploy failed, restoring previous image..."
podman tag localhost/hms-api:rollback localhost/hms-api:dev
systemctl --user restart hms-api-staging.service || true
echo "DEPLOY FAILED - check logs" >&2
exit 1
