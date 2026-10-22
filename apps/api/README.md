# Project Profit API

FastAPI + SQLAlchemy 2 + psycopg 3 + PostgreSQL + Alembic. Stateless instances,
bounded connection pools, all configuration from environment variables. No
SQLite, no local storage, no dependency on Solo Empire.

## Verify (decisive gate)

```bash
cd apps/api
task check            # ruff check + ruff format --check + mypy --strict + pytest
# equivalent without Task:
uv sync --frozen
uv run --frozen ruff check . && uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
```

The tests start a bundled, disposable PostgreSQL 16 (`pgserver`, dev dependency)
in a temp directory, create throw-away databases, and serve a local JWKS with
generated RSA keys and synthetic users. `DATABASE_URL` is never read by the
test-suite. Requires Python 3.12 and `uv` (no Docker/Postgres install needed).

Covered: pure money rules (rounding, negatives, no-time/zero-revenue, JPY,
huge integers); migrations up/down and model drift; JWT rejection (audience,
issuer, expiry, future `iat`, missing claims, `alg` none/HS256, wrong key,
unknown `kid`, JWKS outage, missing config); roles; cross-workspace isolation on
every record route; composite-FK integrity; rate snapshots; per-currency totals
independent of paging; Idempotency-Key required/replay/conflict/concurrent;
deletion without replay resurrection; rollback of failed writes.

## Configuration

See `.env.example` (names/placeholders only).

| Variable | Meaning |
| --- | --- |
| `DATABASE_URL` | Dedicated PostgreSQL (`postgresql://…`; `sslmode=require` for managed hosts) |
| `AUTH_JWKS_URL` | OIDC JWKS endpoint (https; http only for loopback tests) |
| `AUTH_ISSUER` | Exact expected `iss` |
| `AUTH_AUDIENCE` | Exact expected `aud` (the API's resource identifier) |
| `AUTH_LEEWAY_SECONDS`, `AUTH_JWKS_TIMEOUT_SECONDS`, `AUTH_JWKS_CACHE_SECONDS`, `AUTH_JWKS_MIN_REFRESH_SECONDS` | Clock leeway, bounded JWKS fetch timeout, cache TTL, minimum interval between refreshes for unknown `kid` |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`, `DB_STATEMENT_TIMEOUT_MS` | Per-instance pool bounds. Total connections ≈ instances × (size + overflow); keep below the provider limit (use the provider's pooled endpoint if needed) |

The JWKS cache serves keys while younger than `AUTH_JWKS_CACHE_SECONDS`; after a
failed refresh, expired keys are used for at most one more hour, then every
request returns 503 (also during the refresh cooldown).

Missing/invalid auth configuration fails closed: private routes return `503
service_unavailable` (or `401` without a token). There is no development bypass
and identity is never taken from request headers/body.
All HTTP request bodies are capped at 64 KiB before JSON parsing, including
chunked requests and incorrect or missing `Content-Length`. Oversized bodies
return a sanitized 413 `validation_error` with the normal request identifier.

## OIDC setup required

Use any OIDC provider that issues **RS256 JWT access tokens** with claims
`sub`, `iss`, `aud`, `exp`, `iat`. Register an API/resource whose identifier is
`AUTH_AUDIENCE`; make the web BFF obtain access tokens for that audience; point
`AUTH_JWKS_URL` at the provider JWKS (keys need `kid`). Authorization is not
taken from token claims: a workspace creator becomes `owner`, membership is
stored per verified `(iss, sub)` in `workspace_members` (roles `owner`,
`editor`, `viewer`; viewers are read-only). Non-members get `404`.
Member invitation/management endpoints are **not** in this slice; additional
members currently require a database write (see tests) — a known gap.
Use a dedicated API audience, never the web client's ID, and restrict which
clients can obtain that audience at the identity provider. Keep
`AUTH_JWKS_MIN_REFRESH_SECONDS` no greater than `AUTH_JWKS_CACHE_SECONDS`;
the settings currently validate each range separately.

## Run locally

```bash
export DATABASE_URL=...   # a disposable PostgreSQL you own
export AUTH_JWKS_URL=... AUTH_ISSUER=... AUTH_AUDIENCE=...
uv sync --frozen
uv run python -m app.migrate            # explicit migration (never automatic)
uv run uvicorn app.main:app_factory --factory --port 8000
```

`GET /health` – liveness (no secrets, no DB). `GET /ready` – 200 only when auth
configuration is present and valid (configuration only; the OIDC provider is
not contacted) and the database is reachable at the Alembic head revision;
otherwise a sanitized 503 whose `request_id` equals `X-Request-ID`.

## API

Prefix `/v1`; JSON; errors are `{"error":{"code","message","request_id"}}`
(`X-Request-ID` is returned on every response). Codes: `unauthorized` 401,
`forbidden` 403, `not_found` 404, `validation_error` 400/422,
`idempotency_conflict` 409, `service_unavailable` 503, `internal_error` 500.

Routes: `GET/POST /workspaces`, `GET /workspaces/{wid}/summary`,
`GET/POST /workspaces/{wid}/projects`, `GET/PATCH …/projects/{pid}`,
`GET/POST …/projects/{pid}/{time-entries|expenses|payments}`,
`DELETE …/{resource}/{entry_id}` (204, owner/editor).

Notes:
- All creates require `Idempotency-Key` (1–128 chars `[A-Za-z0-9._:-]`). Same
  key + same method/path/body → the original stored response (201, header
  `Idempotent-Replayed: true`; a deleted record is not re-created); different
  request → 409. Keys are scoped to the verified identity (workspace create) or
  the workspace (everything else) and claimed in the same PostgreSQL
  transaction as the write. Rows in `idempotency_keys` should be pruned by an
  operator job after your retry window (e.g. `DELETE … WHERE created_at < now() - interval '7 days'`).
- Money is a decimal string of integer minor units (`0|[1-9][0-9]{0,14}` on
  input). Labor = Σ round-half-up(minutes × snapshot rate / 60) per entry.
  `profit_per_hour_minor` and `margin_percent` round half away from zero (2
  decimals for margin) and are `null` with 0 minutes / 0 receipts.
  `total_minutes` and `minutes` are JSON integers. Totals are SQL aggregates
  over all records, grouped per currency; never cross-currency.
- `client_name` may be empty; `name`/`description` must be non-blank after trim.
- Lists default to `limit=50` (max 100); `total` is exact.
- Workspace list is unpaginated (a user's own memberships).
- Every GET runs in one `REPEATABLE READ`, read-only PostgreSQL transaction, so
  metadata, totals and counts in a response come from a single snapshot (no
  serialization retries needed; writes stay `READ COMMITTED`). `PATCH` returns
  its own just-updated, row-locked project values with its aggregates.

## Deployment (Railway + dedicated PostgreSQL such as Neon)

Nothing has been provisioned or deployed from this repository, and there is
deliberately **no `railway.json`**: Railway's current documentation does not
support it for new services (legacy support ends 2026-12-01) and prefers
infrastructure-as-code, which would need a separately approved plan/apply that
has not been run. Configure the service manually (or via an approved IaC plan):

1. Create a dedicated PostgreSQL database; keep its URL as a Railway secret
   variable `DATABASE_URL`.
2. Create a Railway service from this repository with **source/root directory
   `apps/api`** and the **Dockerfile** builder (non-root uid 10001, no migration
   on start).
3. Set variables `AUTH_JWKS_URL`, `AUTH_ISSUER`, `AUTH_AUDIENCE` (OIDC above)
   plus optional pool tuning from the example environment file.
4. Set the **pre-deploy (release) command** to `python -m app.migrate`, so
   migrations run once per release, never per replica or on container start.
5. Start command: the image default (`uvicorn app.main:app_factory --factory`,
   listening on `$PORT`).
6. Healthcheck path: `/health` (liveness). Treat `GET /ready` as the
   post-release gate: it is 200 only after auth configuration is valid and the
   schema is at head; it is not a pre-config liveness probe.
7. Scale replicas horizontally; pools are per instance.

Manual migration: `DATABASE_URL=… python -m app.migrate` (or
`alembic upgrade head`). The Docker image was not built in this environment
(no Docker available); build it once before relying on it:
`docker build -t project-profit-api apps/api`.

### Rollback and backup

- Before every release migration take a backup/branch/point-in-time restore point
  with the database provider (Neon branch or PITR; managed backups on other
  providers). Test a restore periodically.
- App rollback: redeploy the previous image; migration 0001 is additive. For a
  future migration prefer expand/contract so the previous release still works.
- Schema rollback: `alembic downgrade -1` (tested for 0001→base) **drops data**;
  prefer restoring from the pre-release backup/branch.

## Security/logging

Logs carry only request id, event name and exception class — never tokens,
bodies, SQL or DB URLs; client errors never echo submitted values. Audit rows
(`audit_events`) store actor `(iss, sub)`, action, resource ids, request id and
bounded (≤2 KiB) metadata. No CORS is enabled: browsers talk to the Next.js BFF.

### Error reporting (Sentry, opt-in)

`app/observability.py` turns Sentry on only when `SENTRY_DSN` is set on the host
(optional `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`); without it nothing is sent.
Errors only (no tracing, breadcrumbs or local variables). Unhandled errors and
database errors are reported with the request id; every event passes an allowlist
scrubber (`scrub_event`) that drops request data, users, exception messages and
frame variables. Tested in `tests/test_observability.py`. The web app (BFF) has no
Sentry yet.
