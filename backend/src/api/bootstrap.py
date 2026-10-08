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


def _write_bootstrap_file(password: str) -> None:
    BOOTSTRAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOOTSTRAP_FILE.write_text(
        f"username: admin\npassword: {password}\n"
        "MUST be changed on first login (must_change_password = TRUE).\n"
        "This file is deleted automatically after that password change.\n",
        encoding="utf-8",
    )
    try:
        BOOTSTRAP_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    except (NotImplementedError, OSError):
        pass  # Windows / unusual filesystems


def remove_bootstrap_file() -> None:
    """Called once the bootstrapped admin has chosen their own password."""
    try:
        BOOTSTRAP_FILE.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove bootstrap file %s: %s", BOOTSTRAP_FILE, exc)


async def ensure_admin_user(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        admin_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = TRUE"
        )
        if admin_count > 0:
            logger.info("Admin user already exists — bootstrap skipped")
            return

        password = secrets.token_urlsafe(16)   # ≥ 21 chars

        # Write the file FIRST. If that fails we stop before creating a user
        # whose password nobody could ever read — the next start retries.
        # The password is never logged: logs are shipped to Loki and indexed.
        try:
            _write_bootstrap_file(password)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot write the admin bootstrap file {BOOTSTRAP_FILE} ({exc}). "
                "Set ADMIN_BOOTSTRAP_FILE to a writable path and restart."
            ) from None

        created = await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role, active, must_change_password)
            VALUES ('admin', 'admin@localhost', $1, 'admin', TRUE, TRUE)
            ON CONFLICT (username) DO NOTHING
            """,
            hash_password(password),
        )

    if created != "INSERT 0 1":
        # A user named "admin" exists but isn't an active admin, so the
        # password in the file would never work. Don't leave it around.
        remove_bootstrap_file()
        logger.error(
            "No active admin exists and the username 'admin' is taken by a "
            "non-admin account. Promote an existing user to admin in the database."
        )
        return

    banner = "=" * 70
    logger.warning(banner)
    logger.warning(" INITIAL ADMIN USER CREATED")
    logger.warning("   Username: admin")
    logger.warning("   Password location: file: %s", BOOTSTRAP_FILE)
    logger.warning("   The user is flagged must_change_password=TRUE.")
    logger.warning("   Read the file and log in; it is deleted after the password change.")
    logger.warning(banner)


async def ensure_seed_satellites(pool: asyncpg.Pool, satellite_ids: list[str]) -> None:
    """Register satellites named in SEED_SATELLITES (the bundled simulator's
    fleet). Telemetry is only accepted for registered satellites, so without
    this a fresh docker-compose stack would drop every simulated frame."""
    if not satellite_ids:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO satellites (id, name) VALUES ($1, $1) ON CONFLICT (id) DO NOTHING",
            [(sat_id,) for sat_id in satellite_ids],
        )
    logger.info("Seed satellites ensured: %s", ", ".join(satellite_ids))
