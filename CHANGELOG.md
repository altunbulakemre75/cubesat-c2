# Changelog

## v0.1.1 — security and reliability release

### Security
Fixes four vulnerabilities reported by the DREAM Security Research Team, plus related issues
found while fixing them. Details are in the GitHub Security Advisory.

- **Sessions are validated in the database on every request.**
  - The role is no longer trusted from the JWT.
  - Token revocation moved from Redis to Postgres (`revoked_tokens`), so a Redis outage
    neither revives revoked tokens nor locks users out.
  - Demotion, deactivation and password change end all of a user's sessions.
  - Refresh tokens rotate; a replayed refresh token ends the whole session family.
- **WebSockets** authenticate with single-use 30-second tickets (`POST /auth/ws-ticket`)
  instead of access tokens in the URL. Refresh tokens are no longer accepted. Open sockets
  are re-validated and closed on logout, deactivation, demotion or expiry.
- **NATS requires authentication**, with two least-privilege identities:
  - `backend` owns the bus.
  - `groundstation` can only submit raw frames and ACKs and read the uplink queue.
- **Unregistered satellites:** telemetry for them is dropped, and commands or TLE uploads
  for them return 404. Nothing creates satellites implicitly any more.
- **Two-admin approval** actually waits for the second admin. Critical commands start in
  `awaiting_approval`, and `POST /commands/{id}/approve` releases them.
- **Login rate limiting** is no longer disabled by `DEBUG=true`, and
  `X-Forwarded-For` is only honoured from `TRUSTED_PROXIES`.
- **Audit log** is append-only at the database level (trigger).
- **Admin bootstrap password** is never written to the log (CodeQL finding).
- **Default deployment hardening:**
  - Credentials are generated per install.
  - TimescaleDB, Redis, NATS, Prometheus, Loki and the raw API bind to 127.0.0.1.
  - Redis requires a password.
  - nginx no longer serves `/api/metrics`.
  - `DEBUG` defaults to `false`.
- **CI** uses a read-only `GITHUB_TOKEN`.
- **Frontend dependencies:** production `npm audit` is clean (axios, react-router 7, ...);
  the dev tooling moved to vite 6 and vitest 4.

### Command & control
- **The command loop closes end to end.** The simulator executes commands idempotently
  and ACKs them. ACKs that arrive before the `sent` update, or after a timeout, are no
  longer dropped.
- **Uplink windows:**
  - Commands are scheduled only onto passes over uplink-capable stations (SatNOGS
    stations are receive-only).
  - A pass-planned command whose window was missed is re-planned instead of being sent.
- **Retries** back off 1 s / 4 s / 16 s (max 3). A timeout after loss of signal doesn't
  consume a retry, and a satellite NACK is final.
- **Mode policy:**
  - It is enforced on stale data: an unknown mode or one older than 2 h needs
    `confirm_unverified_mode`.
  - It is re-checked right before transmission.
  - The mode falls back to the database when Redis is unavailable.
- **Idempotency keys** return the original command; reusing a key for a different
  command returns 409. Transitions are race-free.

### Reliability
- **Single leader.** Background services run on one leader elected via a Postgres
  advisory lock; migrations are serialized. This makes `--workers 2` and multiple replicas
  safe.
- **FDIR:**
  - Staleness is pass-aware, and the latest telemetry is read from the database.
  - One alert per fault, with no duplicates after a restart.
  - A TLE refresh keeps a week of pass history.
- **Pass prediction** runs once for all stations, in a worker thread: about 22 s of
  event-loop blocking became about 1.7 s off the loop for 200 stations.
- **JetStream stream:** 7-day retention, one stable durable ACK consumer, and the
  telemetry WebSocket no longer replays the whole stream.
- **Ingestion** NAKs raw frames it couldn't publish, and the writer applies real
  backpressure.

### Found in end-to-end testing
- The first `docker compose up` on fresh volumes could abort the backend while Postgres was
  still initializing (health check over TCP, startup retries).
- Local `.env`/`.venv` files leaked into Docker images (missing `.dockerignore`): the UI
  bundle pointed at `localhost:8000` and broke from other machines.
- `GET /satnogs/observations` returned 500 once observations existed; the metadata was
  double-encoded JSON (fixed, existing rows repaired by migration 010).
- The satellite page requested a non-existent telemetry endpoint; charts now start with
  history and keep growing with live data.
- The 3D globe works without a Cesium Ion account (bundled offline imagery) and falls back
  to it when a token is rejected.

### Developer experience
- Integration tests run against real TimescaleDB and NATS (CI service containers).
- ruff and `mypy --strict` are clean and enforced in CI for the backend and the simulator.
- The bootstrap password lives on a volume (`/var/lib/cubesat/admin_bootstrap`), so it
  survives container recreation.

### Upgrade notes
1. **Existing database volume:** the database keeps the password it was created with. Set it
   in `.env` before the first start, e.g. `POSTGRES_PASSWORD=devpassword` (the v0.1.0 compose
   default). The `secrets` service writes it into the shared secrets volume.
2. **Logins:** a new JWT key is generated unless `JWT_SECRET_KEY` is pinned in `.env`; in
   that case everyone logs in again. If the key is pinned, tokens issued by v0.1.0 (which
   carry no token version) stay valid only until their user's first role, password or
   active-flag change.
3. **WebSocket clients:** they must fetch a ticket from `POST /auth/ws-ticket` and connect
   with `?ticket=`. `?token=` is no longer accepted.
4. **NATS clients other than the backend** (custom ground station bridges) must connect as
   `groundstation`:
   - The password is in the secrets volume (`nats_groundstation_password`).
   - Use `inbox_prefix="_INBOX_gs"`.
5. **Satellites:** satellites that only ever appeared through telemetry are still
   registered (rows aren't deleted). New ones must be created via `POST /satellites` or
   `/satnogs/sync`.
6. **Ground stations:** manually created stations are migrated to `uplink_capable = true`;
   imported SatNOGS stations are receive-only.

## v0.1.0
Initial public release.
