"""SCD Type 2 history transition using a single supplied SQLAlchemy connection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Table, and_, select
from sqlalchemy.engine import Connection

from etlguard.reconcile import LoadCounts


def scd2_merge(
    conn: Connection,
    table: Table,
    rows: list[dict[str, Any]],
    keys: list[str],
    tracked_columns: list[str],
) -> LoadCounts:
    inserted = 0
    updated = 0
    unchanged = 0
    versions = 0
    track = (tracked_columns or [name for name in rows[0] if name not in keys]) if rows else []
    # UTC naive DATETIME(6) is stored consistently without local timezone conversion.
    for row in rows:
        predicate = and_(*(table.c[key] == row[key] for key in keys), table.c.is_current.is_(True))
        current = conn.execute(select(table).where(predicate).with_for_update()).mappings().first()
        if current is None:
            conn.execute(table.insert().values(
                **row, __etl_id=str(uuid4()), effective_from=datetime.now(timezone.utc).replace(tzinfo=None),
                effective_to=None, is_current=True,
            ))
            inserted += 1
        elif any(current[col] != row[col] for col in track):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute(table.update().where(table.c.__etl_id == current["__etl_id"]).values(
                effective_to=now, is_current=False,
            ))
            conn.execute(table.insert().values(
                **row, __etl_id=str(uuid4()), effective_from=now,
                effective_to=None, is_current=True,
            ))
            updated += 1
            versions += 1
        else:
            # A matched, unchanged business key counts as processed for count
            # reconciliation. No physical write occurs for this SCD2 row.
            updated += 1
            unchanged += 1
    return LoadCounts(inserted, updated, unchanged, versions)
