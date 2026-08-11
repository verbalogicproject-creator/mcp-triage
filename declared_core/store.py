"""SQLite store helpers: connect, and install an FTS5 index over declared tables.

`declared_core` sits *on top of* your existing SQLite tables. You bring the base
tables (your data); `install_fts` attaches an FTS5 shadow table + keep-in-sync
triggers for every ``SourceTable`` in your schema, then backfills it from the
rows already present. After that, BM25 queries work.

The FTS5 pattern (external-content table + porter/unicode61 tokenizer + AFTER
INSERT/DELETE/UPDATE triggers) mirrors the battle-tested layout from the source
project this was extracted from.
"""

from __future__ import annotations

import sqlite3

from .schema import CorpusSchema, SourceTable


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open a SQLite connection with the pragmas declared_core expects.

    WAL keeps readers non-blocking; foreign_keys is on for referential links.
    An in-memory DB is the default so tests and demos need no files.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _quote_cols(cols: tuple[str, ...]) -> str:
    return ", ".join(cols)


def install_fts(conn: sqlite3.Connection, schema: CorpusSchema) -> None:
    """Create (idempotently) the FTS5 shadow table + sync triggers for each
    source, then rebuild the index from existing rows. Safe to call on every
    connect."""
    for src in schema.sources:
        _install_one(conn, src)
    conn.commit()


def _install_one(conn: sqlite3.Connection, src: SourceTable) -> None:
    cols = src.text_columns
    col_list = _quote_cols(cols)
    fts = src.fts
    base = src.name

    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
        f"  {col_list},"
        f"  content={base},"
        f"  content_rowid=rowid,"
        f"  tokenize='porter unicode61'"
        f")"
    )

    # `new`/`old` column values, each coalesced to '' so a NULL text column
    # never breaks the trigger.
    new_vals = ", ".join(f"coalesce(new.{c}, '')" for c in cols)
    old_vals = ", ".join(f"coalesce(old.{c}, '')" for c in cols)

    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS {base}_ai AFTER INSERT ON {base} BEGIN\n"
        f"  INSERT INTO {fts}(rowid, {col_list}) VALUES (new.rowid, {new_vals});\n"
        f"END"
    )
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS {base}_ad AFTER DELETE ON {base} BEGIN\n"
        f"  INSERT INTO {fts}({fts}, rowid, {col_list}) "
        f"VALUES('delete', old.rowid, {old_vals});\n"
        f"END"
    )
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS {base}_au AFTER UPDATE ON {base} BEGIN\n"
        f"  INSERT INTO {fts}({fts}, rowid, {col_list}) "
        f"VALUES('delete', old.rowid, {old_vals});\n"
        f"  INSERT INTO {fts}(rowid, {col_list}) VALUES (new.rowid, {new_vals});\n"
        f"END"
    )

    # Backfill: if the base table already had rows (inserted before the FTS
    # existed), 'rebuild' repopulates the index from content=. No-op on empty.
    conn.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
