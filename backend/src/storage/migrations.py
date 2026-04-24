"""
Simple SQL migration runner using asyncpg.
Runs numbered .sql files from the migrations/ directory in order.
Tracks executed migrations in a _migrations table.
"""

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


async def run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id          SERIAL      PRIMARY KEY,
                filename    TEXT        NOT NULL UNIQUE,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        applied: set[str] = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM _migrations")
        }

        sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for sql_file in sql_files:
            if sql_file.name in applied:
                continue

            logger.info("Applying migration: %s", sql_file.name)
            sql = sql_file.read_text(encoding="utf-8")

            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _migrations (filename) VALUES ($1)",
                    sql_file.name,
                )

            logger.info("Migration applied: %s", sql_file.name)

        if not sql_files:
            logger.warning("No migration files found in %s", MIGRATIONS_DIR)
