"""Declared corpus schema — the heart of the generalization.

`declared_core` does not own your data model. You keep your rows in whatever
SQLite tables you already have; you *declare* how to search them. A
`CorpusSchema` is that declaration:

  - which tables are searchable (`SourceTable`),
  - which columns hold searchable text, which to carry into results,
  - which columns cluster items into structural neighbours (kind, batch, tags),
  - which foreign keys link one table to another.

Everything downstream — BM25, structural expansion, fusion — is driven by this
declaration. There is no table name hardcoded anywhere in the engine. Swap the
schema and the same engine indexes a different corpus.

This is the "declared > inferred" thesis applied to retrieval config: you write
the structure of your corpus down, and get determinism + speed + zero setup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Table and column names are interpolated straight into DDL, triggers, and
# SELECTs (an SQL identifier cannot be a bound ``?`` placeholder). They are
# developer-declared, but `declared_core` is a library — a consumer may thread a
# config-driven name through (e.g. frontmatter_rag exposes facet columns as user
# config). Constraining every identifier to a plain SQL identifier closes that
# injection footgun at the one place all three retrieval modules read from.
# ``where`` / ``order_by`` are deliberately *not* validated here: they are
# documented as free-form static SQL predicates (see the field docstrings).
_SQL_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _check_identifier(value: str, field_name: str) -> None:
    if not _SQL_IDENT_RE.match(value):
        raise ValueError(
            f"{field_name} must be a plain SQL identifier "
            f"([A-Za-z_][A-Za-z0-9_]*); got {value!r}"
        )


@dataclass(frozen=True)
class SourceTable:
    """A searchable table in your corpus.

    name          the SQLite table name.
    id_column     primary-key column carried into every result as ``id``.
    text_columns  columns holding searchable prose. These become the FTS5
                  index columns and are matched by BM25.
    carry_columns extra columns to load into each result dict (timestamps,
                  status, path, …) — not searched, just returned.
    fts_table     name of the FTS5 shadow table. Defaults to ``f"{name}_fts"``.
    where         a static SQL predicate ANDed onto every query against this
                  table, e.g. ``"status = 'active'"``. Keep it constant — it is
                  interpolated, never parameterised.
    cluster_columns
                  columns whose *shared scalar value* marks two rows as
                  structural neighbours (e.g. ``kind``, ``batch``). Used by the
                  structural-expansion hop.
    tag_columns   columns holding a JSON array of strings; rows whose arrays
                  *overlap* are structural neighbours (e.g. ``tags``).
    order_by      ordering applied when pulling structural neighbours (e.g.
                  ``"created_at DESC"`` to prefer recent rows). Optional.
    """

    name: str
    id_column: str = "id"
    text_columns: tuple[str, ...] = ()
    carry_columns: tuple[str, ...] = ()
    fts_table: str | None = None
    where: str | None = None
    cluster_columns: tuple[str, ...] = ()
    tag_columns: tuple[str, ...] = ()
    order_by: str | None = None

    @property
    def fts(self) -> str:
        """The FTS5 shadow-table name (declared or defaulted)."""
        return self.fts_table or f"{self.name}_fts"

    @property
    def load_columns(self) -> tuple[str, ...]:
        """Columns to SELECT for a result row: id first, then text + carry,
        de-duplicated, order preserved."""
        seen: set[str] = set()
        cols: list[str] = []
        for c in (self.id_column, *self.text_columns, *self.carry_columns,
                  *self.cluster_columns, *self.tag_columns):
            if c not in seen:
                seen.add(c)
                cols.append(c)
        return tuple(cols)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SourceTable.name must be non-empty")
        if not self.text_columns:
            raise ValueError(
                f"SourceTable({self.name!r}) declares no text_columns — "
                "there is nothing to full-text index"
            )
        # Every field that reaches SQL as a bare identifier is validated; the
        # free-form ``where`` / ``order_by`` predicates are intentionally not.
        _check_identifier(self.name, "SourceTable.name")
        _check_identifier(self.id_column, "SourceTable.id_column")
        if self.fts_table is not None:
            _check_identifier(self.fts_table, "SourceTable.fts_table")
        for group, cols in (
            ("text_columns", self.text_columns),
            ("carry_columns", self.carry_columns),
            ("cluster_columns", self.cluster_columns),
            ("tag_columns", self.tag_columns),
        ):
            for col in cols:
                _check_identifier(col, f"SourceTable.{group}")


@dataclass(frozen=True)
class Link:
    """A foreign-key relationship between two source tables.

    A structural hop follows it: given anchor rows in ``parent``, pull the rows
    in ``child`` whose ``key`` column references an anchor's id. For example
    ``Link("facts", "notes", "note_id")`` pulls the facts crystallised from a
    matched note.
    """

    child: str
    parent: str
    key: str

    def __post_init__(self) -> None:
        # ``key`` is interpolated into the link-expansion SQL as a bare column
        # name; child/parent are checked against declared sources by CorpusSchema.
        _check_identifier(self.key, "Link.key")


@dataclass(frozen=True)
class CorpusSchema:
    """The full declaration for a corpus: its sources, links, and (optionally)
    the dimension schema used by the rules signal.

    sources     the searchable tables.
    links       cross-table foreign-key hops for structural expansion.
    dimensions  the declared dimension schema for the rules signal. ``None``
                → the built-in generic default (see ``dimensions.DEFAULT``).
    small_corpus_threshold
                below this many total candidates, fuse by (intent-weighted)
                RRF; at or above it, fuse by weighted-sum. RRF is rank-only and
                robust when scores live on different scales; weighted-sum is
                cheaper at scale. 100 is the tuned default.
    """

    sources: tuple[SourceTable, ...]
    links: tuple[Link, ...] = ()
    dimensions: object | None = None  # DimensionSchema | None (avoid import cycle)
    small_corpus_threshold: int = 100

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("CorpusSchema needs at least one SourceTable")
        names = [s.name for s in self.sources]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate source table names: {names}")
        by_name = set(names)
        for link in self.links:
            if link.child not in by_name:
                raise ValueError(f"Link.child {link.child!r} is not a declared source")
            if link.parent not in by_name:
                raise ValueError(f"Link.parent {link.parent!r} is not a declared source")

    def source(self, name: str) -> SourceTable:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(name)
