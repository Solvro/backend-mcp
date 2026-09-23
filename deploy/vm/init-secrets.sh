#!/usr/bin/env bash
# One-time creation of everything production needs that must not live in git (spec section 9):
# the backend's docker-secret files and both stacks' env files. Never overwrites an existing
# file, so re-running it is safe; prints file names only, never contents.
#
#   sudo /opt/mcpwr/src/backend-mcp/deploy/vm/init-secrets.sh
set -euo pipefail

SECRETS_DIR="${SECRETS_DIR:-/etc/ml-mcp/secrets}"
MCPWR_HOME="${MCPWR_HOME:-/opt/mcpwr}"
SRC_DIR="${SRC_DIR:-$MCPWR_HOME/src/backend-mcp}"
ML_MCP_ENV_EXAMPLE_URL="${ML_MCP_ENV_EXAMPLE_URL:-https://raw.githubusercontent.com/Solvro/ml-mcp/main/.env.prod.example}"
DEPLOY_GROUP="${DEPLOY_GROUP:-mcpwr-deploy}"
BACKEND_ENV="$MCPWR_HOME/stacks/backend/.env.prod"
ML_MCP_ENV="$MCPWR_HOME/stacks/ml-mcp/.env"

umask 077

# Every temp file/dir this script creates is tracked here (space-separated: no arrays in
# bash 3.2, and no path here ever contains a space) and swept up on exit, success or not.
CLEANUP=""
track() { CLEANUP="$CLEANUP $1"; }
cleanup() {
  # shellcheck disable=SC2086 # CLEANUP is an intentionally word-split list of paths
  rm -rf $CLEANUP
}
trap cleanup EXIT

own() { # <mode> <path>
  chmod "$1" "$2"
  if [ "${MCPWR_SKIP_CHOWN:-0}" != 1 ]; then chown "root:$DEPLOY_GROUP" "$2"; fi
}

write_new() { # <path> <mode>: stdin becomes the file, unless the file already exists
  if [ -e "$1" ]; then
    cat >/dev/null
    printf 'kept    %s\n' "$1"
    return 0
  fi
  local tmp
  tmp=$(mktemp "$(dirname "$1")/.init-secrets.XXXXXX")
  track "$tmp"
  cat >"$tmp"
  own "$2" "$tmp"
  ln "$tmp" "$1" # fails if $1 now exists (including a dangling symlink): never clobber
  rm -f "$tmp"
  printf 'created %s\n' "$1"
}

env_value() { # <file> <KEY>
  sed -n "s/^$2=//p" "$1" | tail -n 1
}

random_secret() { openssl rand -hex 32 | tr -d '\n'; }

generate() { # <description-for-error-message>: prints a checked random secret, or exits 1
  local secret
  secret=$(random_secret) || secret=""
  if [ -z "$secret" ]; then
    printf 'could not generate a secret for %s\n' "$1" >&2
    exit 1
  fi
  printf '%s' "$secret"
}

read_secret() { # <path>: prints a checked non-empty secret file's contents, or exits 1
  local value
  value=$(cat "$1")
  if [ -z "$value" ]; then
    printf '%s is empty; remove the file and re-run this script\n' "$1" >&2
    exit 1
  fi
  printf '%s' "$value"
}

mkdir -p "$SECRETS_DIR" "$(dirname "$BACKEND_ENV")" "$(dirname "$ML_MCP_ENV")"
own 0750 "$SECRETS_DIR"

# 1. Env files from their examples. Humans fill in FRONTEND_URL, SMTP_* and the LLM keys.
write_new "$BACKEND_ENV" 0640 <"$SRC_DIR/.env.prod.example"
if [ -e "$ML_MCP_ENV" ]; then
  printf 'kept    %s\n' "$ML_MCP_ENV"
else
  example=$(mktemp)
  track "$example"
  if ! curl -fsS "$ML_MCP_ENV_EXAMPLE_URL" -o "$example"; then
    printf 'could not download %s\n' "$ML_MCP_ENV_EXAMPLE_URL" >&2
    exit 1
  fi
  write_new "$ML_MCP_ENV" 0640 <"$example"
fi
if [ -z "$(env_value "$ML_MCP_ENV" NEO4J_PASSWORD)" ]; then
  neo4j_pw=$(generate "NEO4J_PASSWORD in $ML_MCP_ENV")
  ml_env_new=$(mktemp "$(dirname "$ML_MCP_ENV")/.init-secrets.XXXXXX")
  track "$ml_env_new"
  NEO4J_PW="$neo4j_pw" awk '
    /^NEO4J_PASSWORD=/ { print "NEO4J_PASSWORD=" ENVIRON["NEO4J_PW"]; done = 1; next }
    { print }
    END { if (!done) print "NEO4J_PASSWORD=" ENVIRON["NEO4J_PW"] }
  ' "$ML_MCP_ENV" >"$ml_env_new"
  own 0640 "$ml_env_new"
  mv -f "$ml_env_new" "$ML_MCP_ENV" # atomic: never leaves a truncated file if killed mid-write
  printf 'set     NEO4J_PASSWORD in %s\n' "$ML_MCP_ENV"
fi

# 2. Database passwords, and the connection URLs built from them. Each secret is generated
# and checked in its own statement (so a failing generator, not just a failing pipeline, is
# caught) before it ever reaches write_new, and read back checked before use so an existing
# empty file from an old failed run is refused rather than silently trusted.
for name in postgres_password mongo_root_password redis_password; do
  pw=$(generate "$SECRETS_DIR/$name") # its own statement: a failed generator must not reach write_new
  printf '%s' "$pw" | write_new "$SECRETS_DIR/$name" 0440
done
pg_user=$(env_value "$BACKEND_ENV" POSTGRES_USER)
pg_db=$(env_value "$BACKEND_ENV" POSTGRES_DB)
mongo_user=$(env_value "$BACKEND_ENV" MONGO_ROOT_USER)
if [ -z "$pg_user" ] || [ -z "$pg_db" ] || [ -z "$mongo_user" ]; then
  printf 'set POSTGRES_USER, POSTGRES_DB and MONGO_ROOT_USER in %s first\n' "$BACKEND_ENV" >&2
  exit 1
fi
postgres_password=$(read_secret "$SECRETS_DIR/postgres_password")
printf 'postgresql+asyncpg://%s:%s@postgres:5432/%s' "$pg_user" "$postgres_password" "$pg_db" |
  write_new "$SECRETS_DIR/database_url" 0440
mongo_root_password=$(read_secret "$SECRETS_DIR/mongo_root_password")
printf 'mongodb://%s:%s@mongo:27017/?authSource=admin' "$mongo_user" "$mongo_root_password" |
  write_new "$SECRETS_DIR/mongo_uri" 0440
redis_password=$(read_secret "$SECRETS_DIR/redis_password")
printf 'redis://:%s@redis:6379' "$redis_password" |
  write_new "$SECRETS_DIR/redis_url" 0440

# 3. The RS256 signing pair; the "previous" public key stays empty until the first rotation.
private="$SECRETS_DIR/jwt_private_key.pem"
public="$SECRETS_DIR/jwt_public_key.pem"
if [ -e "$private" ] && [ -e "$public" ]; then
  printf 'kept    %s\nkept    %s\n' "$private" "$public"
elif [ -e "$private" ] || [ -e "$public" ]; then
  printf 'only one half of the JWT keypair exists in %s; restore or remove it by hand\n' "$SECRETS_DIR" >&2
  exit 1
else
  keys=$(mktemp -d)
  track "$keys"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$keys/private.pem" 2>/dev/null
  openssl pkey -in "$keys/private.pem" -pubout -out "$keys/public.pem" 2>/dev/null
  write_new "$private" 0440 <"$keys/private.pem"
  write_new "$public" 0440 <"$keys/public.pem"
  rm -rf "$keys"
fi
: | write_new "$SECRETS_DIR/jwt_previous_public_key.pem" 0440

# 4. External credentials: created empty, filled by a human with sudoedit.
for name in openai_api_key google_api_key langfuse_secret_key smtp_pass; do
  : | write_new "$SECRETS_DIR/$name" 0440
done

cat <<EOF

Fill in by hand with sudoedit (never paste the values anywhere else):
  $SECRETS_DIR/{openai_api_key,google_api_key,langfuse_secret_key,smtp_pass}  (empty = unused)
  $BACKEND_ENV   FRONTEND_URL, SMTP_HOST, SMTP_USER, SMTP_FROM
  $ML_MCP_ENV    OPENAI_/DEEPSEEK_/GOOGLE_/CLARIN_API_KEY, LANGFUSE_*
EOF
