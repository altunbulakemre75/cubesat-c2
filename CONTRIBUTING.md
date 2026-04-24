# Contributing to CubeSat C2

## Getting started

```bash
git clone <repo-url>
cd cubesat-c2
cp .env.example .env
docker compose up -d timescaledb redis nats
```

## Running tests

```bash
# Backend
cd backend && pip install -e ".[dev]" && pytest tests/ -v

# Simulator
cd simulator && pip install -e ".[dev]" && pytest tests/ -v

# Frontend
cd frontend && npm ci && npx tsc --noEmit && npm run build
```

## Adding a protocol adapter

1. Create `backend/src/ingestion/adapters/yourproto.py`
2. Implement `ProtocolAdapter` (see `base.py`)
3. Register in `backend/src/ingestion/adapters/__init__.py`
4. Add tests in `backend/tests/ingestion/test_yourproto_adapter.py`

No core changes needed.

## Commit style

```
feat(module): short description
fix(module): short description
docs: update readme
test(module): add coverage for X
refactor(module): simplify Y
```

## Pull requests

- One feature per PR
- Tests required for business logic
- Update `docs/YOL_HARITASI.md` checkboxes if completing a roadmap item

## Code standards

- Python: type hints required, `ruff` + `mypy` must pass
- TypeScript: strict mode, no `any`
- No AI/ML dependencies (LangChain, OpenAI, etc.)
- No vendor lock-in (AWS-only services banned)

## Questions?

Open a GitHub Discussion or issue.
