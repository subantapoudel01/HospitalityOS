#!/usr/bin/env python
"""
Fail if the SQLAlchemy models and the migrated schema disagree.

Run from backend/:

    python ../infra/ci/check_schema_drift.py

The failure this catches is quiet and expensive: someone adds a column to a
model, it works locally because their database was created from metadata,
and the migration is never written. Everything passes. Then a deploy runs
`alembic upgrade head` against a real database and the column is missing.

Uses alembic's own comparison rather than generating a throwaway revision
file, so there is nothing to clean up afterwards and no chance of a stray
migration being committed by a failed CI run.
"""
from __future__ import annotations

import asyncio
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

# Importing the model modules is what registers tables on Base.metadata.
# This list must match alembic/env.py - a module missing from either place
# makes its tables invisible to the comparison.
import app.modules.receptionist.models  # noqa: F401
import app.platform.guests  # noqa: F401
import app.platform.models  # noqa: F401
import app.platform.users  # noqa: F401
from app.core.config import settings
from app.core.db import Base

#: Tables alembic knows about but the models deliberately do not define.
IGNORED_TABLES = {"alembic_version"}


def _describe(diff) -> str:
    kind = diff[0] if isinstance(diff, tuple) else str(diff)
    return f"  {kind}: {diff}"


async def main() -> int:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(
                        sync_conn,
                        opts={
                            "compare_type": True,
                            "include_name": lambda name, type_, parent: not (
                                type_ == "table" and name in IGNORED_TABLES
                            ),
                        },
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()

    if not diffs:
        print("OK: models and migrations agree")
        return 0

    print("Models and migrations disagree:\n", file=sys.stderr)
    for diff in diffs:
        print(_describe(diff), file=sys.stderr)
    print(
        "\nGenerate a migration for these changes:\n"
        '  make revision m="describe the change"\n',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
