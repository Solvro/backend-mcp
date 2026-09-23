# Load Testing

The lightweight k6 scenario exercises the gateway endpoints and reports p95
latency separately for login and chat. It also requires at least one `429` from
each endpoint, so rate limiting is visible in the run result.

## Run

Start the normal stack first, then run:

```text
just up
just load
```

The load generator is also available as an opt-in Compose service. This keeps
k6 on the same Docker network as nginx and does not start it during a normal
`just up`:

```text
just up
just load-compose
```

To target another gateway:

```text
just load http://localhost:8080
```

The script is in `tests/load/load_test.js`. The `just load` task requires a
local k6 installation; `just load-compose` pulls the `grafana/k6` image.

## Baseline

The current p95 baseline budgets for this smoke load are:

| Endpoint | p95 budget |
| --- | ---: |
| `POST /auth/login` | < 500 ms |
| `POST /api/chat` | < 2500 ms |

The first run against the target environment establishes the measured baseline.
Copy the `login_p95` and `chat_p95` values printed under `Load baseline` into
the table below, along with the date and commit, so later runs are comparable.

| Date / commit | Login p95 | Chat p95 | Login 429s | Chat 429s |
| --- | ---: | ---: | ---: | ---: |
| 2026-09-07 (`<commit-hash>`) | 29.89 ms | 483.14 ms | 15 | 10 |
