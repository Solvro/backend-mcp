# Deploying ml-mcp-backend

Two compose layers, one nginx config.

| | `just up` (dev) | `just up-prod` |
|---|---|---|
| files | `docker/compose.yml` (+ `--profile dev`) | `compose.yml` + `compose.prod.yml`, `--env-file .env.prod` |
| secrets | throwaway: `docker/.tls/` (self-signed), `docker/.e2e-keys/` (JWT) | files under `SECRETS_DIR` mounted as docker secrets |
| ingress | `:8080` -> 301 -> `https://localhost:8443` | `:80` -> 301 -> `:443` |
| extras | Mailpit, host ports on every service | none; only nginx publishes ports |

## Edge (`gateway/nginx/`)

`nginx.conf` holds upstreams and the CORS maps; `templates/*.template` are rendered by the
image's envsubst step from three variables: `SERVER_NAME`, `CORS_ALLOWED_ORIGIN` (one exact
browser origin) and `PUBLIC_HTTPS_SUFFIX` (`""` in prod, `":8443"` locally).

- TLS 1.2/1.3 from `/run/secrets/tls_cert` + `tls_key`; HSTS on 443 only.
- `/auth/*` -> auth-service (30s), `/api/*` -> chat-service (180s, unbuffered),
  `/health` -> chat, `/health/auth` -> auth, `/.well-known/jwks.json` -> auth, else 404.
  `/health` also answers on plain `:80` for probes.
- CORS is decided here and stripped from upstream responses (a second
  `Access-Control-Allow-Origin` would make browsers reject the response). Preflights never
  reach a service.
- Body cap 1 MiB, mirroring `MAX_REQUEST_BODY_SIZE`. Budget math for the 180s is in the template.

## Production secrets

Create `SECRETS_DIR` (default `/etc/ml-mcp/secrets`, `chmod 700`) with these files, `chmod 400`:

```
postgres_password  mongo_root_password  redis_password
database_url   postgresql+asyncpg://<user>:<postgres_password>@postgres:5432/<db>
mongo_uri      mongodb://<user>:<mongo_root_password>@mongo:27017/?authSource=admin
redis_url      redis://:<redis_password>@redis:6379
jwt_private_key.pem  jwt_public_key.pem  jwt_previous_public_key.pem   (last may be empty)
openai_api_key  google_api_key  langfuse_secret_key  smtp_pass          (empty if unused)
tls_cert.pem  tls_key.pem
```

Services only ever see `*_FILE=/run/secrets/<name>`; `EnvSecretsProvider` reads the file.
`scripts/check_prod_secrets.py` (run by `just lint`) fails the build if the resolved prod
config carries a credential as a plain value, publishes a host port on anything but nginx,
or leaves a service without restart policy, resource limits or a log driver.

Then `cp .env.prod.example .env.prod`, fill the non-secret values, and `just up-prod`.

## Rotating the JWT keypair

1. `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out new_private.pem`,
   `openssl pkey -in new_private.pem -pubout -out new_public.pem`.
2. `mv jwt_public_key.pem jwt_previous_public_key.pem`; install the new pair.
3. `just up-prod` (recreates auth- and chat-service). Tokens signed by the old key keep
   verifying via their `kid`.
4. After `REFRESH_TOKEN_EXPIRE_DAYS` (7), truncate `jwt_previous_public_key.pem` and redeploy.

## Rotating the TLS certificate

Replace `tls_cert.pem`/`tls_key.pem` and `docker compose ... restart nginx` (file secrets are
read at container start). If a load balancer terminates TLS instead, point it at `:80`, set
`PUBLIC_HTTPS_SUFFIX=""` and drop the 301 in the template.
