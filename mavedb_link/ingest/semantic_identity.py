"""Canonical, read-only identity projection for SQLite mirror semantics."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Protocol


class IdentityComparisonError(ValueError):
    """The supplied release identity is missing, unsafe, or inconsistent."""


class _Hash(Protocol):
    def update(self, data: bytes, /) -> None: ...


_VOLATILE_META_COLUMNS = frozenset({"build_utc", "build_duration_s"})


def database_semantic_sha256(database: Path) -> str:
    """Hash all user schema and rows except builder-owned volatile meta values."""
    if not database.is_file() or database.is_symlink():
        raise IdentityComparisonError("semantic database is missing or unsafe")
    digest = hashlib.sha256()
    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            schema_objects = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE type IN ('table', 'index', 'trigger', 'view') "
                # SQLite reserves the sqlite_* namespace for its own implementation objects.
                "AND name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name"
            ).fetchall()
            tables: list[str] = []
            for object_type, name, table_name, ddl in schema_objects:
                if not all(
                    isinstance(value, str) for value in (object_type, name, table_name, ddl)
                ):
                    raise IdentityComparisonError("semantic database has an invalid schema object")
                for value in (object_type, name, table_name, ddl):
                    _hash_projection_value(digest, value)
                if object_type == "table":
                    tables.append(name)
            if not tables:
                raise IdentityComparisonError("semantic database has no application schema")
            for name in tables:
                _hash_table_rows(connection, digest, name)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise IdentityComparisonError("semantic database is not readable") from exc
    return digest.hexdigest()


def _hash_table_rows(connection: sqlite3.Connection, digest: _Hash, name: str) -> None:
    columns = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM pragma_table_xinfo(?) WHERE hidden = 0 ORDER BY cid", (name,)
        )
        if isinstance(row[0], str) and not (name == "meta" and row[0] in _VOLATILE_META_COLUMNS)
    ]
    if not columns:
        raise IdentityComparisonError("semantic database has a table without columns")
    quoted = [f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns]
    table = name.replace('"', '""')
    query = f'SELECT {", ".join(quoted)} FROM "{table}" ORDER BY {", ".join(quoted)}'  # noqa: S608
    for row in connection.execute(query):
        digest.update(b"R")
        for value in row:
            _hash_projection_value(digest, value)
    digest.update(b"E")


def _hash_projection_value(digest: _Hash, value: object) -> None:
    """Write a type-preserving, length-delimited scalar into a semantic hash."""
    if value is None:
        payload = b"N"
    elif type(value) is int:
        payload = b"I" + str(value).encode("ascii")
    elif type(value) is float:
        payload = b"F" + value.hex().encode("ascii")
    elif type(value) is str:
        payload = b"T" + value.encode("utf-8")
    elif type(value) is bytes:
        payload = b"B" + value
    else:
        raise IdentityComparisonError("semantic database has an unsupported value type")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
