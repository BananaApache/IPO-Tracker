"""Applies numbered .sql migrations, in order, exactly once each.

Run it with:  uv run python -m backend.migrate

Deliberately small. The reason to hand-roll this instead of using Alembic is
that the entire mechanism fits on one screen, so during an incident you can
read it rather than trust it. Everything below is either "apply pending SQL" or
a guard against a way that goes wrong.

Rule for the .sql files: never edit one that has been applied. Write a new
numbered migration. The checksum check enforces that.
"""

import asyncio
import hashlib
import sys
from pathlib import Path

import asyncpg

from backend.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# 32-bit key for pg_advisory_lock. Any constant works; it just has to be the
# same in every runner instance.
_LOCK_KEY = 8_675_309

# Not itself a migration: the runner has to be able to ask "what is applied?"
# before any migration has ever run.
_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT        PRIMARY KEY,
    checksum   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def discover() -> list[Path]:
    """Migration files, in lexicographic order -- which the NNN_ prefix makes
    numeric order, up to 999."""
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


async def run() -> int:
    """Apply every pending migration. Returns how many were applied."""
    settings = get_settings()
    connection = await asyncpg.connect(settings.database_dsn)
    try:
        # Session-scoped lock: if two runners start at once (compose restart,
        # two deploy pods), the second blocks here and then finds nothing
        # pending, instead of both applying 001 and one crashing on a
        # duplicate table.
        await connection.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        await connection.execute(_BOOTSTRAP)

        applied: dict[str, str] = {
            row["version"]: row["checksum"]
            for row in await connection.fetch("SELECT version, checksum FROM schema_migrations")
        }

        pending: list[tuple[Path, str, str]] = []
        for path in discover():
            sql = path.read_text()
            digest = checksum(sql)

            if path.name in applied:
                if applied[path.name] != digest:
                    # The file and the live schema no longer describe the same
                    # thing. Refuse rather than guess which one is right.
                    raise SystemExit(
                        f"error: {path.name} was edited after it was applied.\n"
                        f"  applied checksum {applied[path.name][:12]}, "
                        f"file checksum {digest[:12]}.\n"
                        f"  Write a new migration instead of editing this one."
                    )
                continue

            # Catches the merge accident where someone lands 002 after 003 has
            # already run. Applying it now would produce a schema that no fresh
            # database will ever reproduce.
            if applied and path.name < max(applied):
                raise SystemExit(
                    f"error: {path.name} is pending but {max(applied)} is already applied.\n"
                    f"  Renumber it above {max(applied)} so a fresh database "
                    f"reaches the same schema."
                )

            pending.append((path, sql, digest))

        if not pending:
            print("migrations: up to date")
            return 0

        for path, sql, digest in pending:
            # One transaction per migration. Postgres has transactional DDL, so
            # a migration that fails halfway leaves no partial schema behind,
            # and migrations that already succeeded stay applied.
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    path.name,
                    digest,
                )
            print(f"migrations: applied {path.name}")

        return len(pending)
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
