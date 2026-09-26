#!/usr/bin/env bash
# Nightly Postgres + Mongo dumps of the backend-mcp stack (spec section 10). Run as root by
# mcpwr-backup.service. Redis is ephemeral and Neo4j is rebuilt from its dump, so neither is here.
# Restore commands: docs/deploy.md.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/mcpwr}"
KEEP_DAYS="${KEEP_DAYS:-7}"
PROJECT="${PROJECT:-backend-mcp}"

umask 077

container() { # <compose service> -> container id, or nothing
  docker ps -q \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1"
}

dump() { # <service> <file name> <command run inside the container>
  local cid out="$DAY/$2"
  cid=$(container "$1")
  if [ -z "$cid" ]; then
    printf '<3>%s is not running; no %s tonight\n' "$1" "$2" >&2
    return 1
  fi
  if docker exec "$cid" sh -c "$3" >"$out.partial"; then
    mv "$out.partial" "$out"
    printf '<6>wrote %s\n' "$out" >&2
  else
    rm -f "$out.partial"
    printf '<3>%s dump failed\n' "$1" >&2
    return 1
  fi
}

DAY="$BACKUP_DIR/$(date -u +%Y-%m-%d)"
mkdir -p "$DAY"
failed=0
# shellcheck disable=SC2016 # expanded inside the container, not here
dump postgres postgres.dump 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' || failed=1
# shellcheck disable=SC2016 # expanded inside the container, not here
dump mongo mongo.archive.gz \
  'mongodump --quiet --archive --gzip --authenticationDatabase admin -u "$MONGO_INITDB_ROOT_USERNAME" -p "$(cat "$MONGO_INITDB_ROOT_PASSWORD_FILE")"' ||
  failed=1
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +
exit "$failed"
