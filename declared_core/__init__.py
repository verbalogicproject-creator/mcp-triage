"""declared_core — a declared, AI-optional hybrid retrieval engine.

You *declare* your corpus (which tables, which columns, which links) and get
BM25 + structural expansion + reciprocal-rank fusion over it, deterministically.
A dense (semantic) signal is optional and degrades cleanly to pure lexical.

The thesis: write the structure of your corpus down, and get determinism, speed,
offline operation, and $0 cost for free. "The structure is the embedding."

Typical use::

    from declared_core import CorpusSchema, SourceTable, hybrid_query
    from declared_core.store import connect, install_fts

    schema = CorpusSchema(sources=(SourceTable("docs", text_columns=("title", "body")),))
    conn = connect("my.db")
    install_fts(conn, schema)
    result = hybrid_query("how does ranking work", schema, conn)
    for hit in result.hits:
        print(hit["id"], hit["rrf_score"])

See ``declared_core.demo`` for a runnable, zero-setup corpus.
"""

from .dimensions import (
    DEFAULT as DEFAULT_DIMENSIONS,
    DimensionDef,
    DimensionSchema,
    register as register_dimension_scorer,
    score_item,
    score_summary,
)
from .retrieval import (
    HybridResult,
    IntentResult,
    NumpyVectorIndex,
    classify_intent,
    hybrid_query,
    rrf_fuse,
)
from .schema import CorpusSchema, Link, SourceTable
from .store import connect, install_fts

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # schema
    "CorpusSchema",
    "SourceTable",
    "Link",
    # store
    "connect",
    "install_fts",
    # retrieval
    "hybrid_query",
    "HybridResult",
    "classify_intent",
    "IntentResult",
    "rrf_fuse",
    "NumpyVectorIndex",
    # dimensions
    "DEFAULT_DIMENSIONS",
    "DimensionDef",
    "DimensionSchema",
    "score_item",
    "score_summary",
    "register_dimension_scorer",
]
