# Getting Started

## Prerequisites

- Docker + Docker Compose
- 4 GB RAM minimum

## 5-minute setup

```bash
git clone <repo-url>
cd cubesat-c2
cp .env.example .env
docker compose up
```

Open `http://localhost:3000` — login with `admin / admin123` (change immediately).

## What you'll see

- **Dashboard**: satellite list + 3D orbit globe + alerts
- **Satellite detail**: live telemetry charts, command center, pass schedule
- **Pass Schedule**: 24h timeline of ground station contacts

## First satellite

The simulator runs 3 fake satellites by default (`CUBESAT1`, `CUBESAT2`, `CUBESAT3`).
Telemetry appears within ~5 seconds of startup.

To add a real satellite with TLE:

```bash
curl -X POST http://localhost:8000/satellites \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"id": "MYSAT", "name": "My Satellite", "norad_id": 12345}'
```

Then POST the TLE to `/satellites/MYSAT/tle`.

## Sending a command

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -d '{"username":"admin","password":"admin123"}' | jq -r .access_token)

curl -X POST http://localhost:8000/commands \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"satellite_id":"CUBESAT1","command_type":"ping","priority":5}'
```

## Monitoring

- Grafana: `http://localhost:3001` (admin / admin)
- NATS management: `http://localhost:8222`
- Prometheus: `http://localhost:9090`
- API docs: `http://localhost:8000/docs`

## Stopping

```bash
docker compose down          # stop containers, keep data
docker compose down -v       # stop + delete all data
```

## Architecture

See [MIMARI.md](MIMARI.md) for the full system architecture.

## Troubleshooting

**Backend won't start:** Check TimescaleDB is healthy — `docker compose ps`

**No telemetry:** Check simulator logs — `docker compose logs simulator`

**Login fails:** Default credentials are `admin / admin123` — check the DB migration ran.
