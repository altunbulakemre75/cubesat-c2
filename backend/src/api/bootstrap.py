"""
First-run admin bootstrap.

Creates a single admin user with a randomly generated password if and only if
no admin exists in the database. Password is printed to the startup log and
the user is flagged must_change_password so the first login forces a rotation.
"""

import logging
import secrets

import asyncpg

from src.api.auth import hash_password

logger = logging.getLogger(__name__)


async def ensure_admin_user(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        admin_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = TRUE"
        )
        if admin_count > 0:
            logger.info("Admin user already exists — bootstrap skipped")
            return

        password = secrets.token_urlsafe(16)   # ≥ 21 chars
        password_hash = hash_password(password)

        await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role, active, must_change_password)
            VALUES ('admin', 'admin@localhost', $1, 'admin', TRUE, TRUE)
            ON CONFLICT (username) DO UPDATE
              SET password_hash = EXCLUDED.password_hash,
                  role = 'admin',
                  active = TRUE,
                  must_change_password = TRUE
            """,
            password_hash,
        )

        banner = "=" * 70
        logger.warning(banner)
        logger.warning(" INITIAL ADMIN USER CREATED")
        logger.warning("   Username: admin")
        logger.warning("   Password: %s", password)
        logger.warning("   YOU MUST CHANGE THIS PASSWORD ON FIRST LOGIN")
        logger.warning(banner)
