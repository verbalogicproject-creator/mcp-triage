"""A tiny, self-contained demo corpus so the quickstart runs with zero setup.

Two declared sources — ``notes`` (prose) and ``facts`` (claim + reason, linked
back to a note) — populated with a handful of rows about retrieval itself. The
content is deterministic (fixed ids + timestamps) so the README's expected
output stays stable.

    from declared_core.demo import build_demo
    conn, schema = build_demo()               # in-memory
    from declared_core import hybrid_query
    result = hybrid_query("how does ranking work", schema, conn)
"""

from __future__ import annotations

import sqlite3

from .schema import CorpusSchema, Link, SourceTable
from .store import connect, install_fts

DEMO_SCHEMA = CorpusSchema(
    sources=(
        SourceTable(
            name="notes",
            id_column="id",
            text_columns=("title", "body"),
            carry_columns=("kind", "created_at"),
            cluster_columns=("kind",),
            tag_columns=("tags",),
            order_by="created_at DESC",
        ),
        SourceTable(
            name="facts",
            id_column="id",
            text_columns=("claim", "reason"),
            carry_columns=("status",),
            where="status = 'active'",
            tag_columns=("tags",),
        ),
    ),
    links=(Link(child="facts", parent="notes", key="note_id"),),
)

# (id, title, body, kind, tags-json, created_at)
_NOTES = [
    ("n1", "BM25 ranking",
     "BM25 scores a document by how often the query terms appear in it, "
     "damped so repeated terms stop helping, and normalised by length so long "
     "documents are not unfairly favoured.",
     "concept", '["retrieval","lexical","ranking"]', "2026-01-01T00:00:00Z"),
    ("n2", "Reciprocal rank fusion",
     "RRF merges several ranked lists using only rank, not raw score, so lists "
     "on different scales combine cleanly. Each item's fused score is the sum of "
     "one over k plus its rank in each list.",
     "concept", '["retrieval","fusion","ranking"]', "2026-01-02T00:00:00Z"),
    ("n3", "Structural expansion",
     "Given the top lexical matches as anchors, structural expansion pulls in "
     "neighbours that share a category or tag, or are linked by a foreign key. "
     "It surfaces relevant rows that share no query words.",
     "concept", '["retrieval","graph","recall"]', "2026-01-03T00:00:00Z"),
    ("n4", "Dense retrieval is optional",
     "A dense index adds paraphrase recall by comparing embeddings. In "
     "declared_core it is a booster: with no dense index, retrieval is pure "
     "lexical and fully deterministic.",
     "concept", '["retrieval","dense","optional"]', "2026-01-04T00:00:00Z"),
    ("n5", "Declared dimensions",
     "Dimensions are named, interpretable scores between zero and one — a "
     "deterministic rules signal with no model. You can read every score and "
     "know exactly why it is what it is.",
     "concept", '["dimensions","rules","explainable"]', "2026-01-05T00:00:00Z"),
    ("n6", "Intent-adaptive weights",
     "Different questions want different signals. An exact-match query up-weights "
     "BM25; an exploratory query up-weights dense and structural. The mapping is "
     "declared, not learned.",
     "concept", '["intent","fusion","weights"]', "2026-01-06T00:00:00Z"),
    ("n7", "FTS5 gotcha: bare operators",
     "SQLite FTS5 treats bare words like AND and OR as operators, so an "
     "un-sanitised query can raise. declared_core quotes every term and OR-joins "
     "them. A common failure to guard against.",
     "gotcha", '["sqlite","fts5","bug"]', "2026-01-07T00:00:00Z"),
    ("n8", "Local-first and zero-dependency",
     "The core needs nothing beyond the Python standard library — sqlite3 with "
     "FTS5 is built in. It runs offline, on a phone or a server, at no cost.",
     "principle", '["local-first","offline","cost"]', "2026-01-08T00:00:00Z"),
]

# (id, claim, reason, note_id, status, tags-json)
_FACTS = [
    ("f1", "RRF combines ranked lists without score calibration",
     "It uses rank position only, so BM25 and cosine scales never need aligning.",
     "n2", "active", '["fusion"]'),
    ("f2", "Retrieval degrades cleanly to lexical when the embedder is down",
     "dense.search returns an empty list, so fusion runs on BM25 + structural alone.",
     "n4", "active", '["dense","resilience"]'),
    ("f3", "A declared dimension with no scorer is neutral, not an error",
     "score_item returns 0.5 for it, so you can declare dimensions before scoring them.",
     "n5", "active", '["dimensions"]'),
    ("f4", "Sanitising the query prevents FTS5 operator crashes",
     "Every term is quoted and OR-joined before it reaches MATCH.",
     "n7", "active", '["fts5","bug"]'),
]


def build_demo(path: str = ":memory:") -> tuple[sqlite3.Connection, CorpusSchema]:
    """Create the demo corpus and return ``(connection, schema)`` ready to query."""
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notes (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
          kind TEXT, tags TEXT DEFAULT '[]', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS facts (
          id TEXT PRIMARY KEY, claim TEXT NOT NULL, reason TEXT,
          note_id TEXT, status TEXT DEFAULT 'active', tags TEXT DEFAULT '[]'
        );
        """
    )
    conn.executemany(
        "INSERT OR IGNORE INTO notes (id,title,body,kind,tags,created_at) VALUES (?,?,?,?,?,?)",
        _NOTES,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO facts (id,claim,reason,note_id,status,tags) VALUES (?,?,?,?,?,?)",
        _FACTS,
    )
    conn.commit()
    install_fts(conn, DEMO_SCHEMA)
    return conn, DEMO_SCHEMA


def hash_embedder(dim: int = 64):
    """A tiny deterministic bag-of-words embedder for demoing dense retrieval
    without downloading a model. NOT semantic — it hashes tokens into buckets —
    but enough to show the dense signal fusing and degrading. Needs numpy."""
    import re
    import zlib

    _word = re.compile(r"\w+")

    def embed(text: str):
        import numpy as np
        vec = np.zeros(dim, dtype=np.float32)
        toks = _word.findall((text or "").lower())
        if not toks:
            return None
        for t in toks:
            # crc32 is a stable, process-independent hash (builtin hash() is
            # randomised per run) so the demo vectors are reproducible.
            vec[zlib.crc32(t.encode()) % dim] += 1.0
        return vec

    return embed
