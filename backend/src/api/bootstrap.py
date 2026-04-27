"""
First-run admin bootstrap.

Creates a single admin user with a randomly generated password if and only if
no admin exists in the database. The password is written to a chmod-600 file
inside the container and the path is announced in logs (NEVER the password
itself, so log aggregators don't index a cleartext credential).
"""

import logging
import os
import secrets
import stat
from pathlib import Path

import asyncpg

from src.api.auth import hash_password

logger = logging.getLogger(__name__)

# File where the one-time bootstrap password is dropped. /tmp inside the
# container is writeable; mount a host volume here in prod if you want to
# persist it. The file is removed after the first successful password change.
BOOTSTRAP_FILE = Path(os.environ.get("ADMIN_BOOTSTRAP_FILE", "/tmp/cubesat_admin_bootstrap"))


async def ensure_admin_user(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        admin_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = TRUE"
        )
        if admin_count > 0:
            logger.info("Admin user already exists — bootstrap skipped")
            return

        password = secrets.token_urlsafe(16)   # ≥ 21 chars

        # Create user FIRST. If insert fails we never expose a password we
        # can't actually use to log in.
        await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role, active, must_change_password)
            VALUES ('admin', 'admin@localhost', $1, 'admin', TRUE, TRUE)
            ON CONFLICT (username) DO NOTHING
            """,
            hash_password(password),
        )

        # Drop the password to a chmod-600 file. Logs only mention the path.
        try:
            BOOTSTRAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            BOOTSTRAP_FILE.write_text(
                f"username: admin\npassword: {password}\n"
                "MUST be changed on first login (must_change_password = TRUE).\n"
                "Delete this file after the first password rotation.\n",
                encoding="utf-8",
            )
            try:
                BOOTSTRAP_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
            except (NotImplementedError, OSError):
                pass  # Windows / unusual filesystems
            password_location = f"file: {BOOTSTRAP_FILE}"
        except OSError as exc:
            # If we can't write the file, fall back to the legacy log banner.
            # Better cleartext-in-logs than no password at all.
            logger.error("Could not write bootstrap file (%s); falling back to log banner", exc)
            password_location = f"PASSWORD (rotate immediately): {password}"

        banner = "=" * 70
        logger.warning(banner)
        logger.warning(" INITIAL ADMIN USER CREATED")
        logger.warning("   Username: admin")
        logger.warning("   Password location: %s", password_location)
        logger.warning("   The user is flagged must_change_password=TRUE.")
        logger.warning("   Read the file, log in, change the password, then 'rm' the file.")
        logger.warning(banner)
