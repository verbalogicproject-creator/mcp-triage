"""Hybrid retrieval: BM25 + structural + optional dense, RRF-fused.

`hybrid_query` is the single entry point most callers need. The lower-level
pieces (bm25_search, expand_from_anchors, rrf_fuse, classify_intent) are exported
for callers who want to assemble their own pipeline.
"""

from .bm25 import bm25_search, bm25_source, sanitize_query
from .dense import DenseIndex, Embedder, NumpyVectorIndex
from .fusion import HybridResult, hybrid_query
from .intent import (
    INTENT_WEIGHT_PROFILES,
    UNIFORM,
    IntentResult,
    classify_intent,
    weights_for,
)
from .rrf import limit_and_dedupe, rrf_fuse
from .structural import anchor_clusters, expand_from_anchors

__all__ = [
    "hybrid_query",
    "HybridResult",
    "bm25_search",
    "bm25_source",
    "sanitize_query",
    "expand_from_anchors",
    "anchor_clusters",
    "rrf_fuse",
    "limit_and_dedupe",
    "classify_intent",
    "weights_for",
    "IntentResult",
    "INTENT_WEIGHT_PROFILES",
    "UNIFORM",
    "DenseIndex",
    "NumpyVectorIndex",
    "Embedder",
]
