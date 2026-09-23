# Production VM + continuous deployment — design

- **Date:** 2026-09-23
- **Status:** approved in conversation, awaiting written-spec review
- **Repos touched:** `Solvro/backend-mcp` (this repo — hosts the VM tooling), `Solvro/ml-mcp`,
  `Solvro/frontend-mcp`
- **Target:** `mlai@10.21.36.20` (private network, reached over VPN), public name
  `mcpwr.solvro.pl` via Solvro's Coolify proxy

## 1. Goal

Run all three stacks — backend-mcp, ml-mcp, frontend-mcp — on one VM, and have every merge to
`main` that passes CI reach that VM automatically, health-gated, with automatic rollback, and
with the VM itself set up from scripts in git rather than by hand.

**Success criteria**

1. A fresh VM becomes production-ready by running two scripts in this repo and filling in the
   external API keys; re-running either script changes nothing.
2. A merge to `main` in any of the three repos is live on the VM within ~5 minutes of its
   publish job finishing, with no human action.
3. A release that fails its health gate is replaced by the previous release automatically, and
   the failed version is not retried until `main` moves again.
4. Nothing on GitHub can execute code on the VM, and no GitHub credential is stored on it
   (unless P3's read-only-PAT fallback is needed).
5. `http://10.21.36.20/health/live` answers as soon as the backend stack is up, so Solvro's
   devops can wire `mcpwr.solvro.pl` to it by trial and error.
6. Rate limits and quotas see each end user's real IP, not the proxy's or the frontend's.

## 2. Context and constraints (as found on 2026-09-23)

- **No deploy pipeline exists** in any of the three repos. backend-mcp CI lints, tests and
  builds images but publishes nothing; ml-mcp likewise; frontend-mcp has no CI and no Dockerfile.
- **All three repos are public.** A self-hosted runner would be reachable by fork PRs unless
  locked down with an org-level runner group, which needs a Solvro org admin.
- **The VM is private** (`10.21.36.20`); GitHub cannot reach it. It has outbound internet. It is
  a fresh install, `mlai` has sudo, and it is a sandbox ("within reason").
- **The VM hosts both backend-mcp and ml-mcp** (sized for it in ml-mcp's `hardware` notes); they
  talk over the `--internal` network `solvro-mcp-internal`.
- **TLS terminates at Coolify.** Devops will point `mcpwr.solvro.pl` at `10.21.36.20:<port>` once
  something answers there, and cannot give the proxy's source IP up front.
- **The frontend is a Next.js 16 app with a BFF.** Browsers talk only to Next (`/`, `/bff/*`);
  Next calls auth-service and chat-service server-to-server (`AUTH_SERVICE_URL`,
  `CHAT_SERVICE_URL`) and keeps tokens in httpOnly cookies scoped to `/bff`.
- **Production config already exists for the backend:** `docker/compose.prod.yml` (docker
  secrets, restart policies, limits, log rotation) and `docs/deploy.md`.

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Pull-based deploy agent on the VM** (not a self-hosted runner, not Coolify-managed) | Safe with public repos without org-admin help; VM needs outbound HTTPS only; keeps `compose.prod.yml` and docker secrets unchanged |
| D2 | **Auto-deploy on merge to `main`**, health-gated, auto-rollback | Chosen policy; `deploy <sha>` + `pause` covers manual pinning |
| D3 | **All three repos, one mechanism** | chat-service is useless without ml-mcp; the frontend is the public site |
| D4 | **Only the frontend is public.** nginx serves `/`, `/bff/` and `/health*`; `/auth/*`, `/api/*`, JWKS are internal-only | The BFF is the only API client; smallest attack surface |
| D5 | **Deploy status goes to journald only** for now | GitHub Deployments API later; reporting kept behind one function |
| D6 | **Images public on GHCR**, pulled anonymously | No registry credential on the VM; repos are already public. Fallback: read-only PAT in the secrets dir |
| D7 | **Provisioning is an idempotent bash script**, not Ansible | One VM; Ansible would be ceremony |
| D8 | **e2e is not rerun on the `main` push**; it gates the PR as a required check | Publish runs after the push's own lint/test/build/migrations jobs |

## 4. Architecture

```
 browser ──https──▶ Coolify proxy (TLS, mcpwr.solvro.pl)
                        │ http, X-Forwarded-For
                        ▼
 VM 10.21.36.20 ── :80 nginx (backend stack, 10.89.0.10) ─────────────┐
                        │ /  /bff/                     │ /health*      │
                        ▼                              ▼               │
               frontend (Next, 10.89.0.11)        chat-service          │
                        │ server-to-server, X-Forwarded-For             │
                        ├──────────▶ auth-service ── postgres, redis    │
                        └──────────▶ chat-service ── mongo, redis       │
                                          │ solvro-mcp-internal (--internal)
                                          ▼
                                   mcp-server ── neo4j (mcp_network)

 GitHub Actions (hosted) ──push──▶ GHCR  ◀──poll/pull── mcpwr-deploy@{backend,ml-mcp,frontend}.timer
```

## 5. VM layout and provisioning

**`deploy/vm/bootstrap.sh`** (backend-mcp), first run as
`ssh mlai@10.21.36.20 'sudo bash -s -- --ref main' < deploy/vm/bootstrap.sh`; idempotent;
Debian/Ubuntu. Only the script travels over SSH, so its first act is an anonymous clone of
backend-mcp at `--ref` into `/opt/mcpwr/src/backend-mcp`; everything it installs (agent, stack
configs, systemd units, `init-secrets.sh`) comes from that checkout. Later runs:
`sudo /opt/mcpwr/src/backend-mcp/deploy/vm/bootstrap.sh --ref main` (pulls, then reinstalls).

- Installs Docker Engine + compose plugin (Docker's apt repo), `git`, `curl`, `jq`,
  `unattended-upgrades` (security only, **no automatic reboot**).
- `/etc/docker/daemon.json`: `"live-restore": true`, json-file log rotation 10 MB × 5.
- Users: `mlai` stays the human admin. New system user **`mcpwr-deploy`**: no login shell, no
  sudo, member of `docker` (root-equivalent — this separates the agent from a human login, it is
  not a security boundary). Owns `/opt/mcpwr`.
- Creates `solvro-mcp-internal` (`docker network create --internal`) if missing.
- SSH: key-only, no root login — **skipped with a warning unless `mlai` already has an
  `authorized_keys` entry**, so it cannot lock anyone out.
- `ufw`: deny inbound by default, allow 22/tcp. Docker-published ports bypass `ufw`; port 80 is
  left open on the private network until the Coolify proxy IP is known (follow-up F2).
- Installs the agent (§7) and the backup job (§10) from the repo into `/opt/mcpwr/agent`, and
  their systemd units; enables timers only when asked (`--enable-timers`).

**Layout**

```
/opt/mcpwr/
  agent/
    mcpwr-deploy                 the agent (from deploy/agent/)
    stacks/{backend,ml-mcp,frontend}.conf
  src/backend-mcp/               checkout the bootstrap installs from (not a deployed release)
  stacks/backend/
    releases/<sha>/              sparse checkout of that commit (docker/, gateway/)
    current -> releases/<sha>
    .env.prod                    non-secret config, 0640 root:mcpwr-deploy
  stacks/ml-mcp/
    releases/<sha>/  current     (docker/, plus full tree on demand for restore-graph)
    .env                         0640 root:mcpwr-deploy (ml-mcp's plaintext-env convention)
  stacks/frontend/
    releases/<sha>/  current     (deploy/)
  state/
    <stack>.deployed             digest + sha of the running release
    <stack>.previous             sha of the release before it
    <stack>.bad                  digest whose migration or gate failed (cleared when :main moves)
    paused                       presence pauses all automatic deploys
    deploy.lock
/etc/ml-mcp/secrets/             0750 root:mcpwr-deploy, files 0440 root:mcpwr-deploy
/var/backups/mcpwr/<date>/       0700 root
```

## 6. Build and publish

A **`publish`** job on `push` to `main`, GitHub-hosted, `permissions: { contents: read,
packages: write }`, authenticating with `GITHUB_TOKEN` only.

| Repo | Runs after | Images |
|---|---|---|
| backend-mcp | `test`, `build-check`, `migrations` in `main.yaml` | `ghcr.io/solvro/backend-mcp-auth` (auth-service + migrations), `ghcr.io/solvro/backend-mcp-chat` |
| ml-mcp | its lint / test / integration jobs | `ghcr.io/solvro/ml-mcp-server` (`docker/Dockerfile.mcp`) |
| frontend-mcp | new CI workflow: lint, vitest, `next build` | `ghcr.io/solvro/frontend-mcp` |

- **Tags:** `sha-<full 40-char sha>` (immutable) and `main` (moving).
- **Ordering:** push every `sha-` tag of the repo first; move `main` last
  (`docker buildx imagetools create -t …:main …:sha-<sha>`), so the agent never sees a `main`
  whose sibling images are missing.
- **Labels:** `org.opencontainers.image.revision=<sha>`, `org.opencontainers.image.source=<repo URL>`.
- **Platform:** `linux/amd64` (confirm VM arch on first SSH; add `arm64` only if needed).
- **Cache:** `type=gha`.
- **Visibility:** packages are created private; a Solvro package admin makes the four public once.
  Before that, verify no image contains `.env`, `dumps/`, keys or `docker/.e2e-keys`
  (`.dockerignore` + a `docker run --rm <image> find / -name '*.pem' -o -name '.env'` check in
  the plan).

**Compose changes so production pulls instead of building**

- backend `docker/compose.prod.yml`: `image: ghcr.io/solvro/backend-mcp-auth:${RELEASE_TAG:?}`
  on `auth-service` and `migrate`, `ghcr.io/solvro/backend-mcp-chat:${RELEASE_TAG:?}` on
  `chat-service`; `FORWARDED_ALLOW_IPS: 10.89.0.10,10.89.0.11`; nginx switches to the proxy edge
  (§8); the `backend` network gets `ip_range: 10.89.0.128/25`, so Docker's dynamic addresses can
  never take the fixed `10.89.0.10` (nginx) or `10.89.0.11` (frontend).
- ml-mcp: new `docker/compose.prod.yml` — `image: ghcr.io/solvro/ml-mcp-server:${RELEASE_TAG:?}`
  on `mcp-server`, `env_file` pointing at `/opt/mcpwr/stacks/ml-mcp/.env` — and a
  `.env.prod.example` listing only what serving needs (Neo4j, LLM, Langfuse, log level; none of
  the Prefect/OCR pipeline keys).
- frontend-mcp: `Dockerfile` (node 22 alpine, multi-stage, `output: "standalone"` in
  `next.config.ts`, non-root, `:3000`, `HEALTHCHECK` via `wget -qO- localhost:3000/`) and
  `deploy/compose.yml`: joins the external network `backend-mcp_backend` with
  `ipv4_address: 10.89.0.11` and alias `frontend`; `NODE_ENV=production`,
  `AUTH_SERVICE_URL=http://auth-service:8000`, `CHAT_SERVICE_URL=http://chat-service:8000`;
  `restart: unless-stopped`, resource limits, json-file logging like the backend.
- The agent always runs `up -d --no-build`; a missing image fails the deploy instead of building
  on the VM.

## 7. Deploy agent

`deploy/agent/mcpwr-deploy` (bash; needs `docker`, `git`, `curl`, `jq`), run as `mcpwr-deploy`
by `mcpwr-deploy@<stack>.service` (oneshot) from `mcpwr-deploy@<stack>.timer`
(`OnBootSec=60`, `OnUnitActiveSec=60`).

**Per-stack config** (`stacks/<stack>.conf`, shell variables): repo URL, sparse paths, primary
image, all images, compose files (relative to the release dir), `--env-file`, compose project
name, networks that must already exist, whether a migrate step runs, health check.
The project name is pinned (`-p backend-mcp`, `-p ml-mcp`, `-p frontend-mcp`): compose
prefixes volumes with it, so a name derived from the release directory would silently start on
empty volumes. The backend's base `docker/compose.yml` keeps `name: ml-mcp-backend` so
developers' local volumes survive; production always passes `-p backend-mcp`, and so do
`just up-prod` / `down-prod`, so a manual run on the VM targets the same project as the agent
instead of starting a second one.

**The agent does not update itself.** A change under `deploy/` reaches the VM only when someone
re-runs the bootstrap (§5). A broken self-update would stop every stack's deploys at once; a
manual step for a rarely-changing script is the cheaper risk.

**One tick**

1. **Lock and gate.** `flock state/deploy.lock` (one deploy on the box at a time). Exit if
   `state/paused` exists.
2. **Detect.** Anonymous GHCR token → `HEAD /v2/<primary>/manifests/main` → digest. Exit if it
   equals `<stack>.deployed` or `<stack>.bad`.
3. **Pre-flight** (nothing running changes; any failure aborts the tick and it retries next
   minute):
   - pull the primary image, read `org.opencontainers.image.revision` → `<sha>`;
   - shallow sparse `git fetch --depth 1 origin <sha>` into `releases/<sha>` (anonymous HTTPS);
   - check required networks exist (frontend needs `backend-mcp_backend`,
     ml-mcp and backend need `solvro-mcp-internal`);
   - `RELEASE_TAG=sha-<sha> docker compose … pull`.
4. **Migrate** (backend only): `docker compose … run --rm migrate`. Failure aborts before the
   swap and marks the digest bad (a deterministic failure retried every minute would only hammer
   the database); Postgres DDL is transactional, so the schema is untouched and the old release
   keeps running.
5. **Swap.** `docker compose … up -d --no-build --remove-orphans --wait --wait-timeout 180`.
6. **Health gate.**
   - backend: `GET http://127.0.0.1/health` through nginx returns a body whose `status` is
     `ok` or `degraded` (not `unhealthy`) — an ml-mcp outage must not block backend deploys;
   - ml-mcp and frontend: their container healthchecks, enforced by `--wait` (the frontend is
     not gated through nginx, which belongs to the backend stack).
7. **Success:** `current` → `releases/<sha>`, write `<stack>.deployed` and `<stack>.previous`,
   keep the 5 newest release dirs and their images, prune older ones.
8. **Failure after the swap:** redeploy the release that was running (`<stack>.deployed`) from its
   own release dir with its own `RELEASE_TAG` — with `up --no-deps` over every service except
   `migrate`, because the old migration would fail against a schema that is already ahead —
   write the failed digest to `<stack>.bad`, log at `err`. With no previous
   release (first deploy), leave the stack as it is and log.

All output goes to journald; `report()` is the single place a later GitHub Deployments
integration hooks in (F1).

**Manual commands** (as `mcpwr-deploy`, e.g. `sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy …`):

| Command | Effect |
|---|---|
| `<stack> status` | running sha/digest, previous, bad, paused |
| `<stack> deploy <sha>` | run steps 3–8 for `sha-<sha>` now |
| `<stack> rollback` | deploy `<stack>.previous` |
| `pause` / `resume` | stop / restart automatic deploys for all stacks |
| `backend proxies` | distinct peer IPs from nginx's log that sent `X-Forwarded-For` (§8) |

**Team rule (documented, not enforced):** a migration must keep the previous release working
(add first, remove in a later release), otherwise automatic rollback is unsafe.

## 8. Edge behind Coolify

- **Two template sets.** `gateway/nginx/templates/` stays the dev TLS edge (unchanged).
  New `gateway/nginx/templates.proxy/` is the production edge; `compose.prod.yml` mounts it
  instead, publishes `80:80` only, and drops the `tls_cert`/`tls_key` secrets (also removed from
  `check_prod_secrets.py` and `docs/deploy.md`).
- **Server:** `listen 80 default_server; server_name _;` — answers whatever `Host` Coolify (or
  devops testing by IP) sends. No TLS, no redirect.
- **Routes**

| Location | Upstream | Read timeout | Notes |
|---|---|---|---|
| `/bff/` | frontend:3000 | 185 s, `proxy_buffering off` | above chat-service's 180 s answer budget |
| `/` | frontend:3000 | 30 s | also serves the email-link pages `/auth/verify`, `/auth/reset-password` |
| `= /health` | chat-service | 10 s | agent gate, Coolify |
| `= /health/live` | chat-service | 5 s | Coolify healthcheck target |

  Everything else falls to `/` → Next, so `/api/*`, `/auth/login`, `/.well-known/jwks.json`,
  `/metrics`, `/docs` are frontend 404s — no backend route is public.
- **Frontend upstream** via `resolver 127.0.0.11 valid=10s` and `proxy_pass` on a variable, so
  nginx starts and serves `/health` while the frontend is absent or mid-deploy (users get 502 for
  those seconds).
- **Real client IP**
  1. `set_real_ip_from ${TRUSTED_PROXY_CIDR}; real_ip_header X-Forwarded-For;
     real_ip_recursive on;` — `TRUSTED_PROXY_CIDR` defaults to **`127.0.0.1/32`** (trust no
     one) until the Coolify proxy's address is discovered with `mcpwr-deploy backend proxies`.
  2. nginx **overwrites** `X-Forwarded-For` with `$remote_addr` (no client-supplied chain flows
     downstream) and sets `X-Forwarded-Proto https`. This lives in the proxy set's own
     `proxy_common.inc`; the dev set keeps `$proxy_add_x_forwarded_for`/`$scheme`.
  3. The BFF forwards `X-Forwarded-For` on every route (**frontend prerequisite P1**); services
     trust `10.89.0.10,10.89.0.11`.
- **Access log** carries `$remote_addr` and `$http_x_forwarded_for` (for step 1 discovery).
- **Security headers** stay (HSTS is honoured because the browser receives it over Coolify's
  HTTPS). **CORS is not rendered** in the proxy set; `CORS_ALLOWED_ORIGIN` is no longer required
  in prod. `SERVER_NAME` is only used by the TLS set.
- **CI:** the lint job renders both template sets with test values and runs `nginx -t` on each.

## 9. Secrets, config and first bring-up

**`deploy/vm/init-secrets.sh`** (run once as root from `/opt/mcpwr/src/backend-mcp`; never
overwrites an existing file; prints file names, never contents). It first creates any missing
config file from its example — `/opt/mcpwr/stacks/backend/.env.prod` from `.env.prod.example`,
`/opt/mcpwr/stacks/ml-mcp/.env` from ml-mcp's new `.env.prod.example` (downloaded from
`raw.githubusercontent.com/Solvro/ml-mcp/main`) — then:

- generates `postgres_password`, `mongo_root_password`, `redis_password` (`openssl rand`),
  and derives `database_url`, `mongo_uri`, `redis_url` from them and `.env.prod`'s user/db names;
- generates `jwt_private_key.pem` / `jwt_public_key.pem` (RS256, 2048) and an empty
  `jwt_previous_public_key.pem`;
- creates empty `openai_api_key`, `google_api_key`, `langfuse_secret_key`, `smtp_pass` for a
  human to fill with `sudoedit`;
- sets a generated `NEO4J_PASSWORD` in the ml-mcp `.env` if empty; LLM keys (`OPENAI_`,
  `DEEPSEEK_`, `GOOGLE_`, `CLARIN_`) and Langfuse keys are filled by a human.

The frontend has no secrets. In the backend's `.env.prod` a human sets `FRONTEND_URL=https://mcpwr.solvro.pl`, `TRUSTED_PROXY_CIDR`, and
SMTP host/user/from. **SMTP server is unknown**; until set, verification/reset emails fail and
nothing else is affected.

**Neo4j graph.** A fresh volume is empty and the dump (`dumps/graph_export.cypher`, ~68 KB) is
not in git. One-time: `scp` it to the VM, then run `uv run restore-graph` in a throwaway
`ghcr.io/astral-sh/uv:python3.12-bookworm-slim` container from a full ml-mcp checkout of the
deployed sha, on `mcp_network`, with `NEO4J_URI=bolt://neo4j:7687` and the dump mounted
read-only. (`Dockerfile.mcp` does not ship `src/data_pipeline`, so the server image cannot run
it.) Until restored, chat answers "no knowledge" rather than failing.

**First bring-up runbook** (added to `docs/deploy.md`)

1. Merge the publish workflows; confirm `:main` images exist for at least the backend.
2. `bootstrap.sh --ref main` (§5).
3. `sudo /opt/mcpwr/src/backend-mcp/deploy/vm/init-secrets.sh`; fill external keys, SMTP and
   `FRONTEND_URL` in the two generated `.env` files.
4. `bootstrap.sh --enable-timers` → the agent deploys ml-mcp, backend and frontend from `:main`.
5. **Checkpoint:** `curl -fsS http://10.21.36.20/health/live` → 200 → **tell devops.**
6. Restore the graph.
7. After devops connects: `mcpwr-deploy backend proxies` → set `TRUSTED_PROXY_CIDR` →
   `mcpwr-deploy backend deploy <current sha>` (recreates nginx with the new value).

**Known gotcha for devops' testing:** in production the BFF sets `Secure` cookies, so logging in
through plain `http://10.21.36.20` will not stick in a browser; it works once traffic arrives over
`https://mcpwr.solvro.pl`.

## 10. Operations

- **Backups:** `mcpwr-backup.timer` (daily 03:00) → `/var/backups/mcpwr/<date>/`: Postgres
  `pg_dump -Fc`, Mongo `mongodump --archive --gzip`, via `docker compose exec`; 7 days kept.
  Redis is not backed up (ephemeral; AOF survives restarts). Neo4j is not backed up (the dump is
  the source of truth). Restore commands in `docs/deploy.md`; the plan includes one real restore
  on the VM. Backups are on the same VM (follow-up F3).
- **Logs:** containers json-file 10 MB × 5; agent and backup in journald
  (`journalctl -u mcpwr-deploy@<stack>`).
- **Reboots:** Docker enabled at boot, containers `unless-stopped`, timers enabled; unattended
  upgrades never reboot.
- **Disk:** agent prunes images beyond the last 5 releases; nothing builds on the VM.

## 11. Prerequisites outside this work

| # | What | Owner | Blocks |
|---|---|---|---|
| P1 | BFF forwards `X-Forwarded-For` on `/bff/auth/*` (today only `/bff/chat` does) | frontend-mcp | public launch (otherwise login 10/min, register 5/min, forgot-password 3/min are site-wide) |
| P2 | Pages for `/auth/verify` and `/auth/reset-password` (the links auth-service emails) | frontend-mcp | real emails going out |
| P3 | Make the four GHCR packages public | Solvro package/org admin | anonymous pulls (fallback: read-only PAT) |
| P4 | Coolify route `mcpwr.solvro.pl` → `http://10.21.36.20:80`; tell us when connected | devops | public access; `TRUSTED_PROXY_CIDR` discovery |
| P5 | SMTP host and credentials | Solvro | verification / reset emails |
| P6 | Make the E2E check required in backend-mcp branch protection | repo admin | D8 |

## 12. Verification

- **CI:** publish jobs green in all three repos; `nginx -t` on both template sets;
  `check_prod_secrets.py` passes with the proxy edge; image content check (no secrets).
- **Agent, on the VM:**
  1. first deploy of each stack from `:main`;
  2. a no-op tick exits without pulling;
  3. a new `main` deploys within one tick;
  4. a deliberately broken release (e.g. chat-service exits at start) fails the gate, rolls back to
     the previous sha, is recorded in `<stack>.bad` and not retried;
  5. `pause` stops a pending deploy; `deploy <sha>` pins; `resume` continues;
  6. a reboot brings everything back.
- **Edge:** `/health/live` 200 on `:80`; `/`, `/bff/auth/session` served by Next; `/api/chat`,
  `/auth/login`, `/.well-known/jwks.json` are Next 404s; with `TRUSTED_PROXY_CIDR` set, a request
  with `X-Forwarded-For: 1.2.3.4` from the proxy shows `1.2.3.4` in auth-service's rate-limit key,
  and the same header from any other peer does not.
- **Backups:** one timer run produces both dumps; one restore into a scratch database succeeds.
- **To confirm while implementing:** `docker compose up --wait` treats the completed `migrate`
  one-shot as success on the installed compose version (else wait on the long-running services
  explicitly); the frontend container can take `ipv4_address` on the external backend network.

## 13. Follow-ups (out of scope)

- **F1** GitHub Deployments API status reporting (fine-grained PAT, `Deployments: write`).
- **F2** `DOCKER-USER` rule restricting `:80` to the Coolify proxy once its IP is known.
- **F3** Off-box backup copy.
- **F4** Move ml-mcp from a plaintext `.env` to docker secrets.
- **F5** Metrics scraping and alerting (`/metrics` exists, nothing scrapes it).
- **F6** Neo4j backup if the live graph starts diverging from the dump.
- Not on this VM: the Prefect/OCR ingest pipeline. Not part of this work: TST-3.
