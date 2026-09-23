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
  cat >"$1"
  own "$2" "$1"
  printf 'created %s\n' "$1"
}

env_value() { # <file> <KEY>
  sed -n "s/^$2=//p" "$1" | tail -n 1
}

random_secret() { openssl rand -hex 32 | tr -d '\n'; }

mkdir -p "$SECRETS_DIR" "$(dirname "$BACKEND_ENV")" "$(dirname "$ML_MCP_ENV")"
own 0750 "$SECRETS_DIR"

# 1. Env files from their examples. Humans fill in FRONTEND_URL, SMTP_* and the LLM keys.
write_new "$BACKEND_ENV" 0640 <"$SRC_DIR/.env.prod.example"
if [ -e "$ML_MCP_ENV" ]; then
  printf 'kept    %s\n' "$ML_MCP_ENV"
else
  example=$(mktemp)
  if ! curl -fsS "$ML_MCP_ENV_EXAMPLE_URL" -o "$example"; then
    rm -f "$example"
    printf 'could not download %s\n' "$ML_MCP_ENV_EXAMPLE_URL" >&2
    exit 1
  fi
  write_new "$ML_MCP_ENV" 0640 <"$example"
  rm -f "$example"
fi
if [ -z "$(env_value "$ML_MCP_ENV" NEO4J_PASSWORD)" ]; then
  awk -v pw="$(random_secret)" '
    /^NEO4J_PASSWORD=/ { print "NEO4J_PASSWORD=" pw; done = 1; next }
    { print }
    END { if (!done) print "NEO4J_PASSWORD=" pw }
  ' "$ML_MCP_ENV" >"$ML_MCP_ENV.new"
  cat "$ML_MCP_ENV.new" >"$ML_MCP_ENV" # keeps the file's mode and owner
  rm -f "$ML_MCP_ENV.new"
  printf 'set     NEO4J_PASSWORD in %s\n' "$ML_MCP_ENV"
fi

# 2. Database passwords, and the connection URLs built from them.
for name in postgres_password mongo_root_password redis_password; do
  random_secret | write_new "$SECRETS_DIR/$name" 0440
done
pg_user=$(env_value "$BACKEND_ENV" POSTGRES_USER)
pg_db=$(env_value "$BACKEND_ENV" POSTGRES_DB)
mongo_user=$(env_value "$BACKEND_ENV" MONGO_ROOT_USER)
if [ -z "$pg_user" ] || [ -z "$pg_db" ] || [ -z "$mongo_user" ]; then
  printf 'set POSTGRES_USER, POSTGRES_DB and MONGO_ROOT_USER in %s first\n' "$BACKEND_ENV" >&2
  exit 1
fi
printf 'postgresql+asyncpg://%s:%s@postgres:5432/%s' "$pg_user" "$(cat "$SECRETS_DIR/postgres_password")" "$pg_db" |
  write_new "$SECRETS_DIR/database_url" 0440
printf 'mongodb://%s:%s@mongo:27017/?authSource=admin' "$mongo_user" "$(cat "$SECRETS_DIR/mongo_root_password")" |
  write_new "$SECRETS_DIR/mongo_uri" 0440
printf 'redis://:%s@redis:6379' "$(cat "$SECRETS_DIR/redis_password")" |
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
