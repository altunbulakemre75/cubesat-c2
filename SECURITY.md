# Security Policy

## Supported Versions

CubeSat C2 is currently in **beta** (v0.1.x). Only the latest tagged release
on the `main` branch receives security updates.

| Version | Supported |
|---------|-----------|
| v0.1.x  | ✅ (current) |
| < v0.1  | ❌ |

Once the project reaches v1.0, this policy will expand to cover the last two
minor releases.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Use one of these channels instead:

1. **GitHub Private Vulnerability Reporting** (preferred) —
   [Open a report](https://github.com/altunbulakemre75/cubesat-c2/security/advisories/new).
   Only repository maintainers see it.

2. **Email** — `altunbulakemre75@gmail.com` with subject prefix
   `[SECURITY] cubesat-c2:` so it isn't missed.

### What to include

- Affected component (e.g. `backend/src/api/routes/commands.py`)
- Reproduction steps or a proof-of-concept
- Suspected impact (data disclosure, auth bypass, RCE, DoS, etc.)
- Your preferred credit name if the advisory is published

### What to expect

- **Acknowledgement:** within 72 hours.
- **Assessment:** within 7 days — we'll confirm whether the issue is
  reproducible and in scope.
- **Fix + disclosure:** coordinated with the reporter. Typical window is
  14–30 days depending on severity. Critical issues are prioritised.
- **Credit:** reporters are credited in the GitHub Security Advisory and
  release notes unless they prefer to stay anonymous.

### Scope

In scope:
- Authentication and authorization (JWT, RBAC, WebSocket auth)
- Input validation on REST/WebSocket/command boundaries
- SQL injection, XSS, SSRF, path traversal
- Insecure defaults (secrets, passwords, CORS)
- Data exposure via logs or error messages

- The default `docker-compose.yml` — it is the documented installation path,
  so insecure defaults there are in scope

Out of scope:
- Denial of service via obvious self-hosted resource limits
- Social engineering, physical attacks
- The RF link itself (uplink/downlink authentication is a mission design
  topic; see docs/ONERILER.md)
- Vulnerabilities in unmodified third-party dependencies — please report
  those upstream (we handle CVE triage via Dependabot)

## Known security posture

- **Sessions**
  - Every request is validated against the database: role, active flag,
    token version and per-token revocation. Nothing is trusted from JWT claims
    beyond identity, and Redis is not on the auth path.
  - Role change, deactivation, password change and refresh-token replay end
    all sessions of a user.
- **WebSockets** use single-use 30-second tickets bound to the issuing session.
  Open sockets are re-validated and closed when the session ends.
- **RBAC.** Three roles: viewer, operator, admin. Critical commands
  (`separation`, `factory_reset`) need a second, different admin.
- **Message bus.** NATS requires authentication with least-privilege accounts.
  The ground-station identity cannot publish uplink commands, canonical
  telemetry or events, read telemetry, or use the JetStream API. Telemetry for
  unregistered satellites is dropped.
- **Deployment defaults**
  - No default passwords: credentials are generated per install.
  - Internal services (DB, Redis, NATS, Prometheus, Loki, raw API) bind to
    localhost.
  - The JWT secret validator refuses weak values.
- **Login.** bcrypt password hashing. Login is rate-limited per client IP and
  username, and `X-Forwarded-For` is only honoured from `TRUSTED_PROXIES`.
- **Admin bootstrap.** The initial password is written to a 600-mode file on
  a volume, never to logs. The admin must change it on first login, and the
  file is deleted afterwards.
- **Audit log.** Append-only, enforced by a database trigger. It covers
  logins, user/role/active changes, command creation, approval, transitions and
  cancellation, ground station changes, satellite deletion, SatNOGS station
  imports and FDIR alert acknowledgements. (Satellite creation and TLE
  uploads are not audited yet.) A database superuser can still disable the trigger.
- **Dependencies.** Dependabot alerts and CodeQL scanning are enabled. CI runs
  with a read-only `GITHUB_TOKEN`.

### Known limitations

- The uplink (`commands.<satellite>` on NATS) carries no command
  authentication (e.g. HMAC). Anyone holding the ground-station credential, or
  an RF transmitter, can talk to a satellite that doesn't authenticate commands
  itself.
- Kubernetes manifests are experimental and not hardened (no NetworkPolicies,
  no TLS between services).
- Traffic between services inside the compose network is not encrypted.

## Advisories

- **v0.1.0** — session validation, WebSocket authentication and message-bus
  authorization flaws, reported by the DREAM Security Research Team. Fixed in
  v0.1.1; see the GitHub Security Advisory and CHANGELOG.md.

## Thanks

Responsible disclosure helps everyone. If you report a real issue
privately and give us time to fix it, we'll publicly thank you when the
fix ships.
