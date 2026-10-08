"""Create the E2E test user inside the backend container.

    docker compose exec -T -e E2E_USERNAME=... -e E2E_PASSWORD=... \
        backend python - < frontend/e2e/seed_user.py

The UI has no auto-login in production builds, and the bootstrap admin
must change its password first, so the suite logs in as its own user.
"""

import asyncio
import os

import asyncpg

from src.api.auth import hash_password
from src.config import settings


async def main() -> None:
    conn = await asyncpg.connect(settings.asyncpg_dsn)
    try:
        await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES ($1::text, $1::text || '@e2e.local', $2, 'operator')
            ON CONFLICT (username) DO NOTHING
            """,
            os.environ["E2E_USERNAME"], hash_password(os.environ["E2E_PASSWORD"]),
        )
    finally:
        await conn.close()


asyncio.run(main())
