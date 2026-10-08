# CubeSat C2

**Open-source satellite Command & Control — one `docker compose up`, not two days of setup.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://www.python.org)
[![CI](https://github.com/altunbulakemre75/cubesat-c2/actions/workflows/ci.yml/badge.svg)](https://github.com/altunbulakemre75/cubesat-c2/actions/workflows/ci.yml)
[![Status](https://img.shields.io/badge/status-beta-orange.svg)](#status)

A mission control system for CubeSats and small satellites: real-time 3D tracking, pass
prediction, a command lifecycle with safety policies, fault detection and an operator UI —
self-hosted, with no vendor lock-in.

> Built for university space clubs, small-satellite operators and the Turkish space
> ecosystem — useful anywhere you need to track and command an orbiting asset.

---

## Why this project?

| Option | Problem |
|---|---|
| Commercial (STK, FreeFlyer) | Expensive, closed-source, out of reach for students |
| OpenC3 COSMOS | Open-source, but a heavy multi-service install |
| Homemade Python scripts | Every team reinvents the wheel |

CubeSat C2 aims at the gap: one command to a working stack with a simulator, so a team can
learn the operations workflow before they have a satellite in orbit.

---

## Status

**Beta (v0.1.x).** Read this before relying on it:

- ✅ **End-to-end with the bundled simulator.** Telemetry ingest → storage → UI. Commands go
  through approval → scheduling → transmission → satellite ACK → `acked`.
- ✅ **Real orbits.** TLEs from Celestrak/SatNOGS, SGP4 pass prediction, a live globe, and
  SatNOGS observation import.
- ⚠️ **Real satellite telemetry is not decoded into the pipeline yet.** The protocol
  adapters (AX.25, KISS, CCSDS) expect the simulator's JSON payload. Mission-specific binary
  formats need a telemetry definition layer; that is the next major piece of work (see
  [docs/ONERILER.md](docs/ONERILER.md)).
- ⚠️ **No radio integration.** The uplink is a NATS subject (`commands.<sat>`); a ground
  station bridge to an SDR/TNC has to consume it.
- ⚠️ **Kubernetes manifests are experimental.** They have not been validated on a cluster.
- ❌ **Edge / offline leaf-node operation** is designed ([docs/MIMARI.md](docs/MIMARI.md)) but
  not implemented.

---

## Features

### Operations
- **3D live globe.** CesiumJS + satellite.js with orbit trails. Works without a Cesium
  account (bundled offline imagery); set `VITE_CESIUM_TOKEN` in `.env` for
  high-resolution imagery.
- **Pass prediction.** SGP4 over every ground station in a single sweep. Passes over
  stations that can transmit are marked as uplink windows; SatNOGS stations are receive-only.
- **Command lifecycle.**
  - States: `awaiting_approval → pending → scheduled → transmitting → sent → acked`, with
    `timeout → retry` and `dead`.
  - Commands only go out inside an uplink window, unless the operator sets the time.
  - Retries back off 1 s / 4 s / 16 s (max 3).
  - A timeout caused by loss of signal (LOS) doesn't count as a retry.
  - A satellite NACK is final.
- **Two-admin approval.** `separation` and `factory_reset` wait until a *different* admin
  approves them.
- **Policy engine.** Mode-based command restrictions, checked when a command is queued and
  again just before transmission.
  - If the mode is unknown or stale (no telemetry for 2 h), the operator must explicitly
    confirm.
- **Idempotent submission.** Repeating a request with the same `idempotency_key` returns
  the original command.
- **FDIR monitor.** Checks battery and temperature thresholds and missing fields.
  - Staleness is pass-aware: being out of view isn't a fault, but a pass over our own
    station without telemetry is.
  - One alert per fault, with no duplicates after a restart.
  - FDIR raises alerts; it does **not** autonomously command safe mode. The operator
    decides.
- **Anomaly detection.** Statistical z-score on a rolling window with hysteresis and
  cooldown (no ML).

### Data pipeline
- **NATS JetStream message bus.** One stream with 7-day retention; durable consumers.
- **TimescaleDB** hypertable for telemetry; Redis is only a cache.
- **Single leader.** With several workers or replicas, the background services (ingestion,
  scheduler, FDIR, TLE refresh) run on one elected leader (Postgres advisory lock).

### Operator UI
- **Dashboard** with globe, satellite cards and a live alert feed.
- **Satellite detail** with live charts, command history, passes and anomalies.
- **Command center** with approve/reject for critical commands and policy feedback.
- **Pass schedule:** a 24 h timeline that highlights uplink windows.
- **User management:** roles, and disabling an account (ends its sessions immediately).

### Security
See [SECURITY.md](SECURITY.md) for the full posture. In short:
- **Sessions.** Every request is checked against the database (role, active flag,
  revocation, token version). Demotion, deactivation, password change and logout take
  effect immediately. Refresh tokens rotate, and a replayed one ends the session family.
- **WebSockets** use 30-second single-use tickets bound to the session and are re-validated
  while open.
- **NATS** requires authentication. The ground-station identity can only hand in raw
  frames and ACKs and read the uplink queue.
- **No default passwords.** Credentials are generated per install. Internal services listen
  on localhost only.
- **Login** is rate-limited; `X-Forwarded-For` is only trusted from the configured proxy.
- **Audit log** is append-only, enforced by the database.

---

## Screenshots

### Dashboard — live 3D tracking
![Dashboard with ISS on Cesium globe](docs/screenshots/01-dashboard.png)

### Satellite detail — real-time telemetry
![Live battery, temperature, solar charts](docs/screenshots/02-satellite-detail.png)

### Command center — policy-gated dispatch
![Command modal with SAFE mode restrictions](docs/screenshots/03-command-center.png)

### Pass schedule — 24h timeline across ground stations
![Pass timeline](docs/screenshots/04-pass-schedule.png)

---

## Quick start

Requirements: Docker with Docker Compose.

```bash
git clone https://github.com/altunbulakemre75/cubesat-c2.git
cd cubesat-c2
docker compose up -d
```

On first start, a one-shot `secrets` service generates every credential (database, Redis,
NATS, JWT key, Grafana). Nothing ships with a default password.

Read the one-time admin password:

```bash
docker compose exec backend cat /var/lib/cubesat/admin_bootstrap
```

Then:
1. Open `http://localhost:3000` and log in as `admin`.
2. Set your own password. The bootstrap file is deleted after that.

The simulator publishes telemetry for three CubeSats (`CUBESAT1`, `CUBESAT2`, `CUBESAT3`),
and they show up on the dashboard within seconds. They also accept commands such as `ping`,
`mode_change` (`{"mode": "science"}`) and `recovery`, and ACK them.

| What | Where |
|---|---|
| Operator UI | `http://localhost:3000` (reachable from your network) |
| Grafana | `http://localhost:3001` — password: `docker compose run --rm secrets show` |
| API docs | `http://localhost:8000/docs` (this machine only) |
| Prometheus | `http://localhost:9090` (this machine only) |

> **Upgrading from v0.1.0 with an existing database volume?** The database keeps its old
> password. Put it in `.env` (e.g. `POSTGRES_PASSWORD=devpassword`) before the first start.
> See [CHANGELOG.md](CHANGELOG.md).

### Track a real satellite

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Register the ISS and pull its TLE
curl -X POST "http://localhost:8000/satnogs/sync/ISS?norad_id=25544" \
  -H "Authorization: Bearer $TOKEN"
```

The ISS appears on the globe with an orbit trail, and the pass schedule lists its passes
over your ground stations. Add your own station with `POST /stations` (uplink-capable by
default), or import SatNOGS stations (receive-only).

---

## Architecture

```
External sources (SatNOGS, Celestrak, ground station / simulator)
              ↓
Protocol adapters — AX.25, KISS, CCSDS (registry, pluggable)
              ↓
NATS JetStream — telemetry.raw.*, telemetry.canonical.*, commands.*, events.*
              ↓
Leader-elected services — ingestion, writer, scheduler, FDIR, TLE refresh
              ↓
TimescaleDB (+ Redis cache) ← FastAPI + WebSocket (every worker)
              ↓
React + CesiumJS operator UI (served by nginx, which proxies /api and /ws)
```

Subsystem diagrams and rationale: [docs/MIMARI.md](docs/MIMARI.md)

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic v2, asyncpg |
| Orbital mechanics | sgp4, skyfield |
| Message bus | NATS JetStream |
| Database | TimescaleDB (PostgreSQL) |
| Cache | Redis |
| Frontend | React 18, Vite, TypeScript (strict), TailwindCSS |
| 3D globe / charts | CesiumJS + satellite.js / Recharts |
| Observability | Prometheus, Grafana, Loki |
| Deployment | Docker Compose; Kubernetes manifests (experimental) |
| CI | GitHub Actions — ruff, mypy --strict, pytest (with real TimescaleDB + NATS), vitest, build, Playwright |

---

## Tests

```bash
# backend unit tests
cd backend && pytest

# backend integration tests (real database and message bus)
docker run -d --name cubesat-testdb -e POSTGRES_PASSWORD=test \
  -p 127.0.0.1:55432:5432 timescale/timescaledb:latest-pg16
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres pytest
# NATS ACL tests: see backend/tests/integration/test_nats_acl.py

cd simulator && pytest
cd frontend && npm test
```

Integration tests run the real FastAPI app against TimescaleDB and NATS (with the repository's
ACL file). They skip automatically when those aren't configured; CI always runs them.

---

## API

FastAPI docs at `http://localhost:8000/docs`. Highlights:

```
POST   /auth/login | /auth/refresh | /auth/logout | /auth/change-password
POST   /auth/ws-ticket                     single-use WebSocket ticket (30 s)
GET    /satellites            POST /satellites            DELETE /satellites/{id} (admin)
POST   /satellites/{id}/tle                (operator; satellite must be registered)
GET    /telemetry/{id}
POST   /commands                           policy-gated, idempotent
POST   /commands/{id}/approve              second admin for critical commands
PATCH  /commands/{id}/transition           manual override (race-safe)
GET    /passes?satellite_id=               includes uplink_capable
POST   /stations                           (admin)
PATCH  /users/{name}/role | /users/{name}/active   (admin)
GET    /fdir/alerts           POST /fdir/alerts/{id}/ack

WS     /ws/telemetry/{id}?ticket=          live telemetry
WS     /ws/events?ticket=                  FDIR + anomaly events (operator+)
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). A new protocol adapter is a single file in
`backend/src/ingestion/adapters/` plus a registry entry.

## Documentation

- [Architecture](docs/MIMARI.md)
- [Getting started](docs/GETTING_STARTED.md)
- [Roadmap](docs/YOL_HARITASI.md) and [proposals](docs/ONERILER.md)
- [Security policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Author

[Emre Altunbulak](https://github.com/altunbulakemre75). If you deploy this in your
university or company, I'd love to hear about it — open an issue.
