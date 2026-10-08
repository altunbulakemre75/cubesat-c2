# Getting Started

## Prerequisites

- Docker with Docker Compose
- 4 GB RAM

## Setup

```bash
git clone https://github.com/altunbulakemre75/cubesat-c2.git
cd cubesat-c2
docker compose up -d
```

The first start builds the images and runs a one-shot `secrets` service that generates every
credential (database, Redis, NATS, JWT key, Grafana) into the `cubesat-secrets` volume.
There are no default passwords. To pin a value instead, put it in `.env` (see `.env.example`).

Get the one-time admin password:

```bash
docker compose exec backend cat /var/lib/cubesat/admin_bootstrap
```

1. Open `http://localhost:3000` and log in as `admin` with that password.
2. Choose a new password (at least 12 characters).

The bootstrap file is deleted after the change.

## What you'll see

- **Dashboard**: satellite cards, 3D globe, live alerts
- **Satellite detail**: live telemetry charts, commands, passes, anomalies
- **Command center**: queue commands, approve critical ones
- **Pass schedule**: 24 h timeline; uplink windows are highlighted

The simulator runs three satellites (`CUBESAT1`, `CUBESAT2`, `CUBESAT3`). It publishes
telemetry every second and acts like a ground station plus satellite for commands.

## Sending a command

From the UI: Command center → pick a satellite → **Send Command**. Or with curl:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' | jq -r .access_token)

curl -X POST http://localhost:8000/commands \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"satellite_id":"CUBESAT1","command_type":"mode_change",
       "params":{"mode":"science"},
       "scheduled_at":"2026-01-01T00:00:00Z"}'
```

Things to know:
- **Transmit time.** Without `scheduled_at`, a command waits for the next pass over an
  *uplink-capable* ground station. The simulator satellites have no TLE, so give them a
  `scheduled_at` (a time in the past means "now").
- **Unverified mode.** If the satellite's mode is unknown or older than 2 hours, the API
  answers `409`. Re-send with `"confirm_unverified_mode": true` to queue the command
  anyway; it is re-checked before transmission.
- **Two-admin commands.** `separation` and `factory_reset` wait in `awaiting_approval`
  until a *different* admin calls `POST /commands/{id}/approve`.
- **Idempotency.** Send an `idempotency_key` to make retries of the same request safe.

## Adding a real satellite and ground station

```bash
# Register a satellite and pull its TLE (here: the ISS)
curl -X POST "http://localhost:8000/satnogs/sync/ISS?norad_id=25544" \
  -H "Authorization: Bearer $TOKEN"

# Your own ground station (uplink-capable by default)
curl -X POST http://localhost:8000/stations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Club GS","latitude_deg":39.93,"longitude_deg":32.85,"elevation_m":900}'
```

Passes are computed for every active station. Stations imported from SatNOGS
(`POST /satnogs/import-stations`) are receive-only and are never used for commanding.

## Connecting a real ground station

The C2 publishes uplink commands on NATS subject `commands.<satellite_id>` and expects
satellite answers on `commands.ack.<satellite_id>`:

```json
{"command_id": "<uuid>", "ok": true}
{"command_id": "<uuid>", "ok": false, "error": "reason"}
```

Raw downlink frames go to `telemetry.raw.<anything>`. Your bridge connects as the
`groundstation` NATS user:
- password in the secrets volume, file `nats_groundstation_password`;
- `inbox_prefix="_INBOX_gs"`.

See `simulator/src/commands.py` and `simulator/src/publisher.py` for a reference
implementation.

## Monitoring

| Service | URL | Notes |
|---|---|---|
| Grafana | `http://localhost:3001` | user `admin`, password: `docker compose run --rm secrets show` |
| API docs | `http://localhost:8000/docs` | this machine only |
| Prometheus | `http://localhost:9090` | this machine only |
| NATS monitoring | `http://localhost:8222` | this machine only |

## Stopping

```bash
docker compose down          # stop containers, keep data and secrets
docker compose down -v       # stop and delete all data, including generated secrets
```

## Troubleshooting

- **Backend won't start, "password authentication failed".** The database volume was
  created with a different password (e.g. by v0.1.0). Put that password in `.env` as
  `POSTGRES_PASSWORD=...` and run `docker compose up -d` again.
- **No telemetry.** Check `docker compose logs simulator`. Frames for satellites that
  aren't registered are dropped; check `docker compose logs backend | grep unregistered`.
- **Lost the admin password before the first login.** It is still in
  `/var/lib/cubesat/admin_bootstrap` inside the backend container (on a volume).
- **Several backend workers or replicas.** Only one runs the background services (leader
  election); the others serve HTTP and WebSockets.

## Architecture

See [MIMARI.md](MIMARI.md).
