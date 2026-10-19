#!/bin/bash
set -euo pipefail

SHA="${1:?Commit required}"
REPO="$HOME/hms-auto-cd-source"

git -C "$REPO" fetch origin "${2:-auto-cd-prep}"
git -C "$REPO" merge-base --is-ancestor "$SHA" FETCH_HEAD

DIR=$(mktemp -d)
trap 'rm -rf "$DIR"' EXIT
git -C "$REPO" archive "$SHA" | tar -xf - -C "$DIR"

podman build -q -t "localhost/hms-api:$SHA" -f "$DIR/Containerfile" "$DIR"

podman run --rm --network host --env-file "$HOME/api-hms-source/.env" "localhost/hms-api:$SHA" python -c 'from app.main import app; from app.database import engine; assert app.openapi(); c=engine.connect(); assert c.exec_driver_sql("SELECT 1").scalar()==1; c.close()'

echo BUILD_TEST_OK
