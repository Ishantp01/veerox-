"""Backup before a chat merge, and rollback after it.

The merge scripts (``merge_duplicate_contacts`` / ``merge_whatsapp_conversations``)
re-point rows and delete the emptied duplicates, so a bad merge can't be undone
from the app. This takes a row-for-row JSON snapshot of everything a merge can
touch for the customers it will merge, and puts it back on request.

Scope: the given customers (``users``), their conversations and leads, and every
table that has a foreign key to one of those three (messages, engine turns,
campaign targets, follow-up tasks, appointments, memory facts, read markers,
contacts' owner, ...). The tables are found from the models' foreign keys, so a
table added later is covered without touching this file.

Restore is an upsert by primary key: rows the merge deleted come back, rows it
changed return to their saved values. Rows created after the snapshot are left
alone. A restore never deletes anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import Date, DateTime, Numeric, Table, Uuid, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

import apps.api.db.models  # noqa: F401  (registers every table on Base.metadata)
from apps.api.db.base import Base

FORMAT_VERSION = 1
_ROOTS = ("users", "conversations", "leads")
_CHUNK = 400


def _chunks(items: list[Any], size: int = _CHUNK) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _tables() -> dict[str, Table]:
    return Base.metadata.tables


def _referencing(root: str) -> list[tuple[Table, Any]]:
    """(table, fk column) for every foreign key pointing at ``root``.id."""
    found = []
    for table in _tables().values():
        for fk in table.foreign_keys:
            if fk.column.table.name == root and fk.column.name == "id":
                found.append((table, fk.parent))
    return found


def _encode(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _decode(column, value: Any) -> Any:
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, Uuid):
        return UUID(value)
    if isinstance(kind, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(kind, Date):
        return date.fromisoformat(value)
    if isinstance(kind, Numeric) and getattr(kind, "asdecimal", True):
        return Decimal(value)
    return value


def _pk(table: Table):
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        raise RuntimeError(f"table {table.name} has a composite primary key; not supported")
    return cols[0]


async def _fetch(db: AsyncSession, table: Table, condition) -> list[dict[str, Any]]:
    result = await db.execute(select(table).where(condition))
    return [{c.name: row[c.name] for c in table.columns} for row in result.mappings().all()]


async def take_snapshot(db: AsyncSession, user_ids: list[UUID]) -> dict[str, list[dict[str, Any]]]:
    """Every row a merge of these customers can read, move or delete, keyed by table."""
    tables = _tables()
    rows: dict[str, dict[Any, dict[str, Any]]] = {}
    ids: dict[str, set[Any]] = {name: set() for name in _ROOTS}

    def keep(table: Table, found: list[dict[str, Any]]) -> None:
        pk = _pk(table).name
        bucket = rows.setdefault(table.name, {})
        for row in found:
            bucket[row[pk]] = row
            if table.name in ids:
                ids[table.name].add(row[pk])

    users = tables["users"]
    for part in _chunks(list(user_ids)):
        keep(users, await _fetch(db, users, users.c.id.in_(part)))

    # Conversations and leads owned by those customers — and any lead whose
    # transcript link points at one of those conversations.
    for name in ("conversations", "leads"):
        table = tables[name]
        for part in _chunks(list(ids["users"])):
            keep(table, await _fetch(db, table, table.c.user_id.in_(part)))
    leads = tables["leads"]
    for part in _chunks(list(ids["conversations"])):
        keep(leads, await _fetch(db, leads, leads.c.conversation_id.in_(part)))

    for root in _ROOTS:
        for table, column in _referencing(root):
            if table.name in _ROOTS:
                continue  # already collected above
            for part in _chunks(list(ids[root])):
                keep(table, await _fetch(db, table, column.in_(part)))

    return {name: list(bucket.values()) for name, bucket in rows.items()}


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    tables: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.tables.values())


async def write_backup(
    db: AsyncSession, user_ids: list[UUID], label: str, out_dir: Path
) -> BackupInfo:
    """Snapshot ``user_ids`` to ``out_dir/<label>-<UTC timestamp>.json``."""
    snapshot = await take_snapshot(db, user_ids)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}-{stamp}.json"
    payload = {
        "format": FORMAT_VERSION,
        "label": label,
        "created_at": datetime.now(UTC).isoformat(),
        "user_ids": [str(u) for u in user_ids],
        "tables": {
            name: [{k: _encode(v) for k, v in row.items()} for row in rows]
            for name, rows in snapshot.items()
        },
    }
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    return BackupInfo(path, {name: len(rows) for name, rows in snapshot.items()})


def load_backup(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != FORMAT_VERSION:
        raise RuntimeError(f"unsupported backup format: {payload.get('format')!r}")
    tables = _tables()
    out: dict[str, list[dict[str, Any]]] = {}
    for name, rows in payload["tables"].items():
        table = tables.get(name)
        if table is None:
            raise RuntimeError(f"backup has table {name!r} which no longer exists")
        out[name] = [
            {col: _decode(table.c[col], value) for col, value in row.items() if col in table.c}
            for row in rows
        ]
    return out


@dataclass(frozen=True)
class RestorePlan:
    missing: dict[str, int]  # rows the merge deleted (will be re-created)
    changed: dict[str, int]  # rows whose values differ from the snapshot
    same: dict[str, int]

    @property
    def needs_restore(self) -> bool:
        return any(self.missing.values()) or any(self.changed.values())


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _same(saved: dict[str, Any], current: dict[str, Any]) -> bool:
    for col, value in saved.items():
        now = current.get(col)
        if isinstance(value, datetime) and isinstance(now, datetime):
            if _utc(value) != _utc(now):
                return False
        elif value != now:
            return False
    return True


async def plan_restore(db: AsyncSession, snapshot: dict[str, list[dict[str, Any]]]) -> RestorePlan:
    tables = _tables()
    missing: dict[str, int] = {}
    changed: dict[str, int] = {}
    same: dict[str, int] = {}
    for name, saved_rows in snapshot.items():
        table = tables[name]
        pk = _pk(table).name
        current: dict[Any, dict[str, Any]] = {}
        for part in _chunks([r[pk] for r in saved_rows]):
            for row in await _fetch(db, table, table.c[pk].in_(part)):
                current[row[pk]] = row
        m = c = s = 0
        for saved in saved_rows:
            now = current.get(saved[pk])
            if now is None:
                m += 1
            elif _same(saved, now):
                s += 1
            else:
                c += 1
        missing[name], changed[name], same[name] = m, c, s
    return RestorePlan(missing, changed, same)


async def restore_snapshot(db: AsyncSession, snapshot: dict[str, list[dict[str, Any]]]) -> int:
    """Upsert every saved row, parents before children. Caller commits.

    Returns the number of rows written. Raises if, after writing, any saved row
    is still missing or different (so the caller can roll back)."""
    tables = _tables()
    dialect = db.bind.dialect.name  # type: ignore[union-attr]
    insert = (sqlite if dialect == "sqlite" else postgresql).insert
    # Parents first so foreign keys are satisfied.
    order = [t.name for t in Base.metadata.sorted_tables if t.name in snapshot]
    written = 0
    for name in order:
        table = tables[name]
        pk = _pk(table).name
        for part in _chunks(snapshot[name], 200):
            for row in part:
                stmt = insert(table).values(**row)
                updates = {k: v for k, v in row.items() if k != pk}
                stmt = (
                    stmt.on_conflict_do_update(index_elements=[pk], set_=updates)
                    if updates
                    else stmt.on_conflict_do_nothing(index_elements=[pk])
                )
                await db.execute(stmt)
                written += 1
    await db.flush()
    after = await plan_restore(db, snapshot)
    if after.needs_restore:
        raise RuntimeError(f"restore check failed: missing={after.missing} changed={after.changed}")
    return written


def user_ids_in(snapshot: dict[str, list[dict[str, Any]]]) -> list[UUID]:
    return [row["id"] for row in snapshot.get("users", [])]


__all__ = [
    "BackupInfo",
    "RestorePlan",
    "load_backup",
    "plan_restore",
    "restore_snapshot",
    "take_snapshot",
    "user_ids_in",
    "write_backup",
]
