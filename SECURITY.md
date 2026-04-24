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

Out of scope:
- Denial of service via obvious self-hosted resource limits
- Issues in the example `docker-compose.yml` used only for local development
- Social engineering, physical attacks
- Vulnerabilities in unmodified third-party dependencies — please report
  those upstream (we handle CVE triage via Dependabot)

## Known security posture

- JWT secret validator refuses weak values in production (see
  `backend/src/config.py`)
- Admin password is randomly generated on first startup and flagged
  `must_change_password`
- WebSocket endpoints require JWT and role verification
- Passwords hashed with bcrypt (cost 12); no plaintext storage
- Audit log is append-only and covers login, user mutation, and satellite deletion
- Dependabot alerts + CodeQL scanning enabled on this repository

## Thanks

Responsible disclosure helps everyone. If you report a real issue
privately and give us time to fix it, we'll publicly thank you when the
fix ships.
