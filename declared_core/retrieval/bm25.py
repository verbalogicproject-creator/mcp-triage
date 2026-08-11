"""BM25 over the declared sources, via SQLite FTS5.

FTS5's MATCH grammar is strict — bare punctuation raises, and bare words like
``AND``/``OR``/``NOT`` are operators. We tokenise the query, quote every term,
and OR-join them so each term contributes and nothing is interpreted as an
operator. Each source in the `CorpusSchema` is queried independently; results
are tagged with their source name in the ``table`` field and carry the declared
columns.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..schema import CorpusSchema, SourceTable

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

#: A possessive marker is grammar, not a search term. Stripped BEFORE tokenising —
#: both the straight and the typographic apostrophe.
#:
#: WHY THIS IS NOT COSMETIC. The tokeniser splits on non-word characters, so
#: ``gemini-3.5-flash's`` becomes ``gemini · 3 · 5 · flash · s`` — one token more than
#: ``gemini-3.5-flash``. Terms are then OR-joined, so that stray ``s`` becomes a full
#: query term, and a bare ``s`` matches any document containing it standing alone.
#:
#: Measured on a 364-document corpus: **124 documents (34%) contain the token ``s``**.
#: A term true of a third of the corpus carries almost no ordering information while
#: still consuming weight — the same near-constant dilution this engine's own dimension
#: guidance warns about (``docs/05-dimensions-the-rules-signal.md``), arriving through
#: the tokeniser instead of through a declared dimension.
#:
#: Observed end-to-end before the fix, on a corpus of near-identical model documents:
#:     "gemini-3.5-flash"                              -> correct document, rank 1
#:     "gemini-3.5-flash's capabilities"               -> WRONG document
#:     "What capabilities does gemini-3.5-flash have?" -> correct document, rank 1
#:     "What is gemini-3.5-flash's knowledge cutoff?"  -> WRONG document
#: The apostrophe-s was the entire difference between the two pairs.
_POSSESSIVE_RE = re.compile(r"(\w)['’][sS]\b")

#: MEASURED AND REJECTED — a collapsed identity token (2026-07-26).
#:
#: The obvious next move after the possessive fix was to give each dotted/hyphenated
#: identifier a collapsed single-token form: ``gemini-3.5-flash`` → ``gemini35flash``,
#: emitted alongside the split tokens here and declared in the corpus. The reasoning was
#: sound and the mechanism verifiably fired:
#:
#:   * FTS5's default tokeniser shreds ``gemini-3.5-flash`` into ``gemini · 3 · 5 ·
#:     flash``, and none of those distinguishes — ``gemini`` is in 17 of 17 model cards,
#:     and ``3`` also matches veo-3.1 and lyria-3.
#:   * The collapsed token appeared in exactly 2 of 365 documents — maximally
#:     discriminating, the opposite of the near-constant dilution that sank the α-leg
#:     dimensions and the retrieval facets.
#:   * **It worked at this layer.** With the token, the correct card moved from BM25
#:     rank 3 to rank 1 (score −8.910 → −13.344).
#:
#: **And it changed retrieval recall by exactly nothing:** discordant (0,0) at k=1, 2
#: and 4, in both intent-on and intent-off conditions, and −1 at k=8. So it was reverted,
#: exactly as the facets were.
#:
#: WHY, AND THIS IS THE TRANSFERABLE PART: **RRF fuses RANKS, not scores.** A document's
#: contribution is ``1/(60 + rank)``, so promoting it from rank 3 to rank 1 moves its
#: term from 1/63 to 1/61 — a ~3% change, swamped by every other leg. A large,
#: real, correctly-aimed improvement *inside* one leg is nearly invisible after fusion.
#:
#: The lesson is not "identifier tokens are useless" — it is that **an intervention must
#: be aimed at the stage that decides the outcome.** Here that stage was intent routing,
#: not tokenisation: disabling it on the same corpus moved recall@4 from 11/20 to 16/20,
#: five one-directional flips. Optimising the leg while the fusion discards the
#: difference is work that measures as zero.


def sanitize_query(query: str) -> str:
    """Return a MATCH-safe query with terms quoted and OR-joined. Empty → ''.

    Possessive markers are removed first, so ``"X's capabilities"`` searches for ``X``
    rather than for ``X`` OR ``s``. See ``_POSSESSIVE_RE`` for why that mattered.
    """
    text = _POSSESSIVE_RE.sub(r"\1", query or "")
    tokens = _TOKEN_RE.findall(text)
    quoted = [f'"{t}"' for t in tokens if t]
    return " OR ".join(quoted)


def bm25_source(
    conn: sqlite3.Connection,
    src: SourceTable,
    query: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Run BM25 against one declared source. Lower ``bm25()`` rank = better."""
    q = sanitize_query(query)
    if not q:
        return []

    cols = src.load_columns
    select_cols = ", ".join(f"base.{c}" for c in cols)
    where = f"{src.fts} MATCH ?"
    params: list[Any] = [q]
    if src.where:
        where += f" AND ({src.where})"

    sql = (
        f"SELECT {select_cols}, bm25({src.fts}) AS _rank "
        f"FROM {src.fts} JOIN {src.name} AS base ON base.rowid = {src.fts}.rowid "
        f"WHERE {where} "
        f"ORDER BY _rank LIMIT ?"
    )
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"table": src.name}
        for col, val in zip(cols, row[:-1]):
            item[col] = val
        item["id"] = row[cols.index(src.id_column)]
        item["bm25_rank"] = row[-1]
        out.append(item)
    return out


def bm25_search(
    conn: sqlite3.Connection,
    schema: CorpusSchema,
    query: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """BM25 across every source in the schema, concatenated."""
    hits: list[dict[str, Any]] = []
    for src in schema.sources:
        hits.extend(bm25_source(conn, src, query, limit=limit))
    return hits
