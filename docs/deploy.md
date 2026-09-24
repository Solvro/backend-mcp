# Deploying ml-mcp-backend

| | `just up` (dev) | production (mcpwr VM) |
|---|---|---|
| how | `docker/compose.yml` + `--profile dev` | the deploy agent, from GHCR images (`just up-prod <sha>` by hand) |
| project | `ml-mcp-backend` | `backend-mcp` |
| secrets | throwaway: `docker/.tls/` (self-signed), `docker/.e2e-keys/` (JWT) | files in `/etc/ml-mcp/secrets`, mounted as docker secrets |
| ingress | `:8080` -> 301 -> `https://localhost:8443` | plain `:80` behind Coolify, which terminates TLS for `mcpwr.solvro.pl` |
| extras | Mailpit, host ports on every service | none; only nginx publishes a port |

Design: `docs/superpowers/specs/2026-09-23-vm-continuous-deployment-design.md`.

## Edge (`gateway/nginx/`)

`nginx.conf` holds the upstreams, the CORS maps and the access-log format; two template sets are
rendered by the image's envsubst step.

**Dev, `templates/`** (from `SERVER_NAME`, `CORS_ALLOWED_ORIGIN`, `PUBLIC_HTTPS_SUFFIX`):

- TLS 1.2/1.3 from `/run/secrets/tls_cert` + `tls_key`; HSTS on 443 only.
- `/auth/*` -> auth-service (30s), `/api/*` -> chat-service (180s, unbuffered),
  `/health` -> chat, `/health/auth` -> auth, `/.well-known/jwks.json` -> auth, else 404.
- CORS is decided here and stripped from upstream responses.

**Production, `templates.proxy/`** (from `TRUSTED_PROXY_CIDR` only):

- Plain HTTP on :80, `server_name _`. No TLS, no redirect: Coolify terminates TLS.
- `/bff/` -> frontend (185 s, unbuffered), `/health` and `/health/live` -> chat-service,
  everything else -> frontend. The backend APIs are not public: only the frontend's BFF reaches
  them, over the internal network.
- `set_real_ip_from ${TRUSTED_PROXY_CIDR}`: only Coolify's proxy may name the client. nginx then
  **overwrites** `X-Forwarded-For` with that one address and sends `X-Forwarded-Proto: https`.
  The frontend (fixed IP `10.89.0.11`) forwards it; auth- and chat-service trust
  `10.89.0.10,10.89.0.11` (`FORWARDED_ALLOW_IPS`).
- The access log's `peer=` / `xff=` fields are how you find Coolify's address (below).

Body cap 1 MiB everywhere, mirroring `MAX_REQUEST_BODY_SIZE`.

## Production VM

`mlai@10.21.36.20` (VPN), public name `mcpwr.solvro.pl`. Three stacks — `backend-mcp`, `ml-mcp`,
`frontend-mcp` — each updated by `mcpwr-deploy@<stack>.timer`:

1. CI publishes `ghcr.io/solvro/<image>:sha-<commit>` and moves `:main` last.
2. Every minute the agent compares `:main`'s digest with what it deployed. On a change it checks
   out that commit's compose files, pulls, runs migrations (backend), `up --wait`s, and gates on
   `GET /health` (`ok` or `degraded`).
3. A failed gate rolls back to the previous release (never re-running migrations) and marks the
   digest bad until `main` moves again. A failed migration marks it bad and touches nothing.

### Migrations must keep the previous release working

Automatic rollback runs the previous release against the *current* schema. Add columns/tables
first, stop using them in a later release, drop them in a later one still.

### First bring-up

Prerequisites: this repo's `publish` job has pushed `:main`; the GHCR packages are public
(`docker logout ghcr.io && docker pull ghcr.io/solvro/backend-mcp-auth:main` works); your SSH key is
in `mlai`'s `~/.ssh/authorized_keys`.

```bash
# 1. Provision (Docker, deploy user, directories, agent, units, network, ssh, firewall)
ssh mlai@10.21.36.20 'sudo bash -s -- --ref main' < deploy/vm/bootstrap.sh

# 2. Secrets and env files (on the VM). Prints file names only.
sudo /opt/mcpwr/src/backend-mcp/deploy/vm/init-secrets.sh
sudoedit /etc/ml-mcp/secrets/openai_api_key          # and the other empty ones you use
sudoedit /opt/mcpwr/stacks/backend/.env.prod         # FRONTEND_URL, SMTP_*
sudoedit /opt/mcpwr/stacks/ml-mcp/.env               # LLM keys, LANGFUSE_*

# 3. Start deploying
sudo /opt/mcpwr/src/backend-mcp/deploy/vm/bootstrap.sh --ref main --enable-timers
journalctl -fu mcpwr-deploy@backend

# 4. Checkpoint - from your laptop on the VPN; then tell devops the edge is up
curl -fsS http://10.21.36.20/health/live
```

The frontend's ticks fail pre-flight (`required network backend-mcp_backend does not exist yet`)
until the backend's first deploy has created it; that is expected and retried every minute.

**Load the knowledge graph** (a fresh Neo4j volume is empty; the dump is not in git):

```bash
# laptop, in the ml-mcp checkout
scp dumps/graph_export.cypher mlai@10.21.36.20:/tmp/graph_export.cypher
# VM
sha=$(sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy ml-mcp status | awk '/^deployed:/ {print $2}')
sudo git clone -q https://github.com/Solvro/ml-mcp.git /tmp/ml-mcp && sudo git -C /tmp/ml-mcp checkout -q "$sha"
sudo docker run --rm --network ml-mcp_mcp_network \
  --env-file /opt/mcpwr/stacks/ml-mcp/.env -e NEO4J_URI=bolt://neo4j:7687 \
  -e PIPELINE_HOST_DUMP_DIR=/dump -v /tmp/graph_export.cypher:/dump/graph_export.cypher:ro \
  -v /tmp/ml-mcp:/src -w /src ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv run --frozen restore-graph
sudo rm -rf /tmp/ml-mcp /tmp/graph_export.cypher
```

**Trust Coolify's proxy** once devops has connected `mcpwr.solvro.pl`:

```bash
sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend proxies   # "<count> <peer ip>"
sudoedit /opt/mcpwr/stacks/backend/.env.prod                          # TRUSTED_PROXY_CIDR=<ip>/32
sha=$(sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend status | awk '/^deployed:/ {print $2}')
sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend deploy "$sha"   # recreates nginx
```

Until then every visitor shares one rate-limit bucket. In production the BFF sets `Secure`
cookies, so logging in over plain `http://10.21.36.20` does not stick in a browser; it works over
`https://mcpwr.solvro.pl`.

### Day to day

```bash
alias mcpwr='sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy'
mcpwr backend status                  # deployed / previous / bad / paused
mcpwr backend deploy <full sha>       # deploy now (ignores pause and bad marks)
mcpwr backend rollback                # previous release, no migrations
mcpwr pause                           # stop automatic deploys (all stacks); `mcpwr resume`
journalctl -u mcpwr-deploy@backend    # what the agent did
```

Changed something under `deploy/`? Re-run the bootstrap (`--ref main`); the agent does not update
itself.

### Production secrets

`/etc/ml-mcp/secrets` (0750 root:mcpwr-deploy, files 0440), created by `init-secrets.sh`:

```
postgres_password  mongo_root_password  redis_password          generated
database_url  mongo_uri  redis_url                              built from the above
jwt_private_key.pem  jwt_public_key.pem                         generated RS256 pair
jwt_previous_public_key.pem                                     empty until the first rotation
openai_api_key  google_api_key  langfuse_secret_key  smtp_pass  empty = unused; fill by hand
```

Services only ever see `*_FILE=/run/secrets/<name>`. `scripts/check_prod_secrets.py` (run by
`just lint`) fails if the resolved prod config carries a credential as a plain value, publishes a
host port on anything but nginx, or leaves a service without restart policy, resource limits or
a log driver.

### Backups

`mcpwr-backup.timer` (03:00) writes `/var/backups/mcpwr/<date>/postgres.dump` and
`mongo.archive.gz` and keeps a week. They live on the same VM: they cover mistakes and bad
migrations, not losing the machine.

```bash
pg=$(docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=postgres)
mg=$(docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=mongo)
mcpwr pause
# Postgres
sudo docker exec -i "$pg" sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < /var/backups/mcpwr/<date>/postgres.dump
# Mongo
sudo docker exec -i "$mg" sh -c 'mongorestore --quiet --archive --gzip --drop --authenticationDatabase admin -u "$MONGO_INITDB_ROOT_USERNAME" -p "$(cat "$MONGO_INITDB_ROOT_PASSWORD_FILE")"' \
  < /var/backups/mcpwr/<date>/mongo.archive.gz
mcpwr resume
```

### Rotating the JWT keypair

1. `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out new_private.pem`,
   `openssl pkey -in new_private.pem -pubout -out new_public.pem`.
2. In `/etc/ml-mcp/secrets`: `mv jwt_public_key.pem jwt_previous_public_key.pem`; install the new
   pair as `jwt_private_key.pem` / `jwt_public_key.pem` (0440 root:mcpwr-deploy).
3. `mcpwr backend deploy <deployed sha>` (recreates auth- and chat-service). Tokens signed by the
   old key keep verifying via their `kid`.
4. After `REFRESH_TOKEN_EXPIRE_DAYS` (7), truncate `jwt_previous_public_key.pem` and redeploy.

### TLS

Production TLS belongs to Coolify. The self-signed pair from `just tls-selfsigned` only serves
`just up`.
