"""Structural expansion — the graph signal without an explicit graph.

Most corpora don't ship an edge table, but they carry *implicit* structure:
rows that share a ``kind``/``batch``/category, rows whose tag lists overlap, and
rows joined by a foreign key. `declared_core` reads those relationships straight
off the declared schema:

  - ``cluster_columns``  → neighbours share a scalar value (same kind, same batch)
  - ``tag_columns``      → neighbours' JSON tag arrays overlap
  - ``links``            → children referencing a matched parent (fact ← episode)

Given the BM25 top-N as anchors, structural expansion pulls in the items one hop
away along any of these. It surfaces relevant rows that share *no query terms*
with the question — the recall that pure lexical search misses.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..schema import CorpusSchema, SourceTable

_MAX_TAG_PATTERNS = 20


def anchor_clusters(schema: CorpusSchema, anchors: list[dict[str, Any]]) -> dict[str, set]:
    """Map each declared cluster column → the set of values seen in the anchors.
    Handy as ``ctx["anchor_clusters"]`` for the hop_distance dimension."""
    cols: set[str] = set()
    for src in schema.sources:
        cols.update(src.cluster_columns)
    out: dict[str, set] = {c: set() for c in cols}
    for a in anchors:
        for c in cols:
            if a.get(c) is not None:
                out[c].add(a[c])
    return {c: v for c, v in out.items() if v}


def expand_from_anchors(
    conn: sqlite3.Connection,
    schema: CorpusSchema,
    anchors: list[dict[str, Any]],
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Pull items one structural hop from the anchors, per the declared schema."""
    if not anchors:
        return []

    by_table: dict[str, list[dict[str, Any]]] = {}
    for a in anchors:
        by_table.setdefault(str(a.get("table")), []).append(a)

    found: dict[tuple[str, Any], dict[str, Any]] = {}

    # 1. Cluster + tag neighbours within each source.
    for src in schema.sources:
        src_anchors = by_table.get(src.name, [])
        if not src_anchors:
            continue
        _expand_clusters(conn, src, src_anchors, limit, found)
        _expand_tags(conn, src, src_anchors, limit, found)

    # 2. Cross-table links: children referencing a matched parent.
    for link in schema.links:
        parent_anchors = by_table.get(link.parent, [])
        parent_ids = [a["id"] for a in parent_anchors if a.get("id") is not None]
        if not parent_ids:
            continue
        child = schema.source(link.child)
        _expand_link(conn, child, link.key, parent_ids, limit, found)

    return list(found.values())[:limit]


def _load(src: SourceTable, row: tuple, hop: str) -> dict[str, Any]:
    cols = src.load_columns
    item: dict[str, Any] = {"table": src.name, "structural_hop": hop}
    for col, val in zip(cols, row):
        item[col] = val
    item["id"] = row[cols.index(src.id_column)]
    return item


def _select_prefix(src: SourceTable) -> str:
    return f"SELECT {', '.join(src.load_columns)} FROM {src.name}"


def _order_limit(src: SourceTable) -> str:
    """ORDER BY + LIMIT, with a deterministic fallback when none is declared.

    WHY THE FALLBACK IS NOT OPTIONAL. Structural expansion selects neighbours under a
    ``LIMIT``, so when a row has more neighbours than the limit, SOME subset comes back.
    With no ``ORDER BY`` that subset is whatever SQLite yields first — rowid order, which
    is insertion order. The engine then returns different results for the same query over
    the same documents depending on the order they happened to be ingested.

    Measured on a 20-document corpus ingested two different ways: **32 of 70 queries (46%)
    returned a different top-4.** The bm25 and rules legs were byte-identical across both
    runs; only the structural leg differed, and everything downstream moved with it. That
    silently breaks invariant 4 — determinism — through an input nobody declares and no
    test pins.

    It is worse than a wrong number, because it is a number that changes for reasons the
    experimenter cannot see. One architecture comparison in this program read 71/75 versus
    75/75 purely from ingest order before this was found.

    THE ID IS APPENDED EVEN WHEN AN ``order_by`` IS DECLARED, and that is the part that
    actually mattered. ``frontmatter_rag`` declares ``order_by="last_updated DESC"``, so a
    fallback-only fix changed nothing — the declared ordering was already there. But
    measured on the same 20 documents, ``last_updated`` had **exactly one distinct value**:
    every card carried the same date. A sort key that is constant across the corpus ties
    every row, and SQLite then falls through to rowid, which is ingest order.

    **A declared ordering on a near-constant column is equivalent to no ordering** — the
    same near-constant failure this engine's dimension guidance warns about, arriving
    through ORDER BY instead of through a weight. So the id is always appended: the
    declared key expresses intent, the id makes ties deterministic.
    """
    declared = f"{src.order_by}, " if src.order_by else ""
    return f" ORDER BY {declared}{src.id_column} LIMIT ?"


def _expand_clusters(conn, src, src_anchors, limit, found) -> None:
    for col in src.cluster_columns:
        values = {a[col] for a in src_anchors if a.get(col) is not None}
        if not values:
            continue
        placeholders = ",".join("?" * len(values))
        where = f"{col} IN ({placeholders})"
        if src.where:
            where += f" AND ({src.where})"
        sql = f"{_select_prefix(src)} WHERE {where}{_order_limit(src)}"
        for row in conn.execute(sql, (*values, limit)).fetchall():
            item = _load(src, row, hop=col)
            found.setdefault((src.name, item["id"]), item)


def _expand_tags(conn, src, src_anchors, limit, found) -> None:
    for col in src.tag_columns:
        collected: set[str] = set()
        for a in src_anchors:
            collected.update(_parse_tags(a.get(col)))
        # SORTED BEFORE TRUNCATION, and that is not cosmetic. This was
        # ``set(list(tags)[:_MAX_TAG_PATTERNS])`` — a SET converted to a list and cut to 20.
        # Python randomises string hashing per process, so *which* 20 tags survived changed
        # from run to run, which changed the structural expansion, which changed the
        # ranking.
        #
        # Measured on the eco-index corpus, 49 queries, identical code and inputs: recall@1
        # alternated between 0.6327 and 0.6122 across processes, and a downstream McNemar
        # verdict flipped with it — discordant (5,0) p=0.0625 versus (6,0) p=0.0312, i.e.
        # the same experiment landing on either side of alpha=0.05 depending on the hash
        # seed. Pinning PYTHONHASHSEED made four consecutive runs identical, which is what
        # identified the cause.
        #
        # This is the SECOND non-determinism of this family in this module. The first was
        # `_order_limit` selecting neighbours under a LIMIT with no stable sort key. Both
        # have the same shape: **a cut applied to an unordered collection.** Any truncation
        # needs a total order first, or the cut is arbitrary and silently irreproducible.
        tags = sorted(collected)[:_MAX_TAG_PATTERNS]
        if not tags:
            continue
        clauses = " OR ".join(f"{col} LIKE ?" for _ in tags)
        params = [f'%"{t}"%' for t in tags]
        where = f"({clauses})"
        if src.where:
            where += f" AND ({src.where})"
        sql = f"{_select_prefix(src)} WHERE {where}{_order_limit(src)}"
        for row in conn.execute(sql, (*params, limit)).fetchall():
            item = _load(src, row, hop="tag")
            found.setdefault((src.name, item["id"]), item)


def _expand_link(conn, child, key, parent_ids, limit, found) -> None:
    placeholders = ",".join("?" * len(parent_ids))
    where = f"{key} IN ({placeholders})"
    if child.where:
        where += f" AND ({child.where})"
    sql = f"{_select_prefix(child)} WHERE {where}{_order_limit(child)}"
    for row in conn.execute(sql, (*parent_ids, limit)).fetchall():
        item = _load(child, row, hop=f"link:{key}")
        found.setdefault((child.name, item["id"]), item)


def _parse_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    try:
        parsed = json.loads(str(raw))
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []
