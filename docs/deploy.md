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
  **overwrites** `X-Forwarded-For` with that one address and sends `X-Forwarded-Proto: https`,
  `X-Forwarded-Host: $host` and no `Forwarded` header, whatever the client sent.
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
# 1. Provision (Docker, deploy user, directories, agent, units, network, ssh, firewall).
#    Needs NOPASSWD sudo for mlai (stdin is the script, so sudo cannot ask for a password);
#    otherwise: scp deploy/vm/bootstrap.sh mlai@10.21.36.20:/tmp/bootstrap.sh
#               ssh -t mlai@10.21.36.20 sudo bash /tmp/bootstrap.sh --ref main
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

`init-secrets.sh` creates the backend's env file and every secret first and the ml-mcp `.env`
last, from ml-mcp's `.env.prod.example` on its `main`. If that is not published yet it prints
`WARNING: … not available yet; re-run this script after ml-mcp's main has it` and still exits 0:
re-run it once it is, which creates only the ml-mcp `.env` (with a generated `NEO4J_PASSWORD`)
and leaves every existing file alone. Until then the ml-mcp ticks fail and retry.

**If the first deploy fails** there is nothing to roll back to: the digest is marked bad and the
next ticks skip it. Fix the cause, then `mcpwr <stack> deploy <sha>` (below), or merge the fix.

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
sudoedit /opt/mcpwr/stacks/backend/.env.prod                          # TRUSTED_PROXY_CIDR=<ip>/32, one value
sha=$(sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend status | awk '/^deployed:/ {print $2}')
sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend deploy "$sha"   # recreates nginx
```

`TRUSTED_PROXY_CIDR` is an env var in `.env.prod`, so this redeploy *does* change compose's
config hash and nginx really is recreated — unlike a secret file's content, below.

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

- A `rollback` sticks: the release rolled back from is marked bad, so the ticks leave it alone
  until `main` moves (or you `deploy` its sha explicitly).
- A `deploy <sha>` of anything but `main`'s commit lasts only until the next tick returns to
  `main`; `mcpwr pause` first to pin it.
- For the backend, `deploy <older sha>` fails at `migrate` once the schema is ahead of that
  release; use `rollback` (which never migrates) instead.
- `deploy` and `rollback` wait up to 15 minutes for a deploy already in progress, then fail.

Changed something under `deploy/`? Re-run the bootstrap (`--ref main`); the agent does not update
itself.

### Production secrets

`/etc/ml-mcp/secrets` (0750 root:mcpwr-deploy, files 0444 root:mcpwr-deploy), created by
`init-secrets.sh`:

```
postgres_password  mongo_root_password  redis_password          generated
database_url  mongo_uri  redis_url                              built from the above
jwt_private_key.pem  jwt_public_key.pem                         generated RS256 pair
jwt_previous_public_key.pem                                     empty until the first rotation
openai_api_key  google_api_key  langfuse_secret_key  smtp_pass  empty = unused; fill by hand
```

The files are world-readable on purpose. Compose's file secrets are read-only bind mounts that
keep the host owner and mode, and auth/chat/migrate (and mongo, after dropping privileges) read
them as uid 999, which is neither root nor in `mcpwr-deploy`. 0444 is still safe: on the host the
0750 directory admits only root and `mcpwr-deploy` (already root-equivalent through the `docker`
group), and a container only sees the secret files mounted into it.

Services only ever see `*_FILE=/run/secrets/<name>`. `scripts/check_prod_secrets.py` (run by
`just lint`) fails if the resolved prod config carries a credential as a plain value, publishes a
host port on anything but nginx, or leaves a service without restart policy, resource limits or
a log driver.

### Backups

`mcpwr-backup.timer` (03:00) writes `/var/backups/mcpwr/<date>/postgres.dump` and
`mongo.archive.gz` and keeps a week. They live on the same VM: they cover mistakes and bad
migrations, not losing the machine.

`docker ps`/`docker exec` need `sudo` (only `mcpwr-deploy` is in the `docker` group), and the dump
files under `/var/backups/mcpwr` (0700 root) must be read by `sudo` too, not by your shell's own
redirection. `mcpwr pause` only stops the *automatic* deploy ticks — auth-service and chat-service
keep writing to the databases the whole time, so stop them for the restore itself.

```bash
pg=$(sudo docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=postgres)
mg=$(sudo docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=mongo)
auth=$(sudo docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=auth-service)
chat=$(sudo docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=chat-service)
mcpwr pause
sudo docker stop "$auth" "$chat"
# Postgres
sudo cat /var/backups/mcpwr/<date>/postgres.dump |
  sudo docker exec -i "$pg" sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists'
# Mongo
sudo cat /var/backups/mcpwr/<date>/mongo.archive.gz |
  sudo docker exec -i "$mg" sh -c 'mongorestore --quiet --archive --gzip --drop --authenticationDatabase admin -u "$MONGO_INITDB_ROOT_USERNAME" -p "$(cat "$MONGO_INITDB_ROOT_PASSWORD_FILE")"'
sudo docker start "$auth" "$chat"
mcpwr resume
```

### Rotating the JWT keypair

1. Generate the new pair on the VM, readable by you only:
   ```bash
   (umask 077
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out new_private.pem
    openssl pkey -in new_private.pem -pubout -out new_public.pem)
   ```
2. Install it, keeping the current public key as the previous one:
   ```bash
   d=/etc/ml-mcp/secrets
   sudo mv "$d/jwt_public_key.pem" "$d/jwt_previous_public_key.pem"
   sudo install -m 0444 -o root -g mcpwr-deploy new_private.pem "$d/jwt_private_key.pem"
   sudo install -m 0444 -o root -g mcpwr-deploy new_public.pem "$d/jwt_public_key.pem"
   shred -u new_private.pem new_public.pem   # they must not linger in your home directory
   ```
3. Restart auth-service and chat-service directly — `mcpwr backend deploy` does **not** do this
   for you: a secret file is a bind mount by path, so a content change (unlike an env var) never
   changes compose's config hash, and `deploy` only recreates containers whose hash changed.
   `sudo docker restart` the two containers instead, found the same way as in Backups above
   (`--filter label=com.docker.compose.service=auth-service` / `=chat-service`). Tokens signed by
   the old key keep verifying via their `kid`.
4. After `REFRESH_TOKEN_EXPIRE_DAYS` (7) — a new shell by then, so spell the path out:
   ```bash
   sudo truncate -s 0 /etc/ml-mcp/secrets/jwt_previous_public_key.pem
   ```
   and restart the same two containers again — not `mcpwr backend deploy`, for the same reason
   as step 3.

### TLS

Production TLS belongs to Coolify. The self-signed pair from `just tls-selfsigned` only serves
`just up`.
