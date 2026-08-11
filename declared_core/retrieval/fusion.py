"""Hybrid retrieval orchestrator — the one function most callers use.

    hybrid_query(query, schema)
        ├── bm25_search(schema, query)          lexical, across all sources
        ├── expand_from_anchors(top_bm25)       structural (cluster / tag / link)
        ├── dense.search(query)                 optional semantic booster
        ├── score_item(...) per candidate       the declared rules signal
        └── fuse the 4 ranked lists
              small corpus → intent-weighted RRF   (rank-only, robust)
              large corpus → intent-weighted sum    (cheaper at scale)

Every hit carries its dimension scores, its ``rules_score``, its ``rrf_sources``
(which signals found it), and — when dense ran — its ``dense_score``. Nothing is
hidden: you can always see why a result ranked where it did.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..dimensions import DEFAULT as DEFAULT_DIMS
from ..dimensions import DimensionSchema, score_item, score_summary
from ..schema import CorpusSchema
from . import intent as _intent
from .bm25 import bm25_search
from .dense import DenseIndex
from .rrf import limit_and_dedupe, rrf_fuse
from .structural import anchor_clusters, expand_from_anchors

# Provenance labels for the four fused lists, in the exact order they are built
# below (bm25, structural, rules, dense). Passed to rrf_fuse so each signal's
# contribution to rrf_sources is labelled by the caller, not sniffed from the
# item (the rules list carries merged copies that would otherwise mislabel).
_LIST_LABELS = ("bm25", "structural", "rules", "dense")


@dataclass
class HybridResult:
    """The outcome of one query: the ranked hits + how they were produced."""

    query: str
    total_candidates: int
    mode: str  # 'rrf' | 'rrf+dense' | 'weighted' | 'weighted+dense'
    intent: str
    hits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "total_candidates": self.total_candidates,
            "mode": self.mode,
            "intent": self.intent,
            "hits": self.hits,
        }


def hybrid_query(
    query: str,
    schema: CorpusSchema,
    conn: sqlite3.Connection,
    *,
    limit: int = 25,
    dense: DenseIndex | None = None,
    use_intent: bool = True,
    weights: tuple[float, float, float, float] | None = None,
) -> HybridResult:
    """Run lexical + structural (+ optional dense) retrieval and fuse.

    query       the natural-language or keyword query.
    schema      the CorpusSchema declaring what to search.
    conn        an open SQLite connection with FTS installed (see store).
    limit       how many hits to return, and the per-signal candidate cap.
    dense       an optional DenseIndex for semantic recall. ``None`` → lexical.
    use_intent  route fusion weights by query intent. ``False`` → uniform RRF.
    weights     explicit ``(bm25, structural, rules, dense)`` weights that OVERRIDE both the
                intent profile and UNIFORM. ``None`` → existing behaviour, unchanged.

    WHY EXPLICIT WEIGHTS EXIST. Until now the only ways to influence fusion were the intent
    profile and the uniform default, both chosen *inside* the engine. That makes per-corpus
    fitting inexpressible: a fitting procedure has to **produce a weights object** and hand
    it to the engine, and it cannot monkey-patch a module and call the result a deliverable.

    It also closes a correctness hole. A caller carrying its own declared weights had no way
    to pass them, so a wrapper would accept a weights object and silently drop it — the same
    "a declared policy quietly becomes a different one" failure that ``reallocate_absent``
    exists to prevent, arriving one layer up. `kg_rag_pipeline.retrieve.rank` hit exactly
    that during Phase D2 and six of its tests caught it.

    Explicit weights deliberately SKIP ``reallocate_absent``: a caller stating all four
    numbers has already decided the ratio, and adjusting it underneath them would reintroduce
    the silent substitution this parameter removes.
    """
    dims: DimensionSchema = schema.dimensions or DEFAULT_DIMS  # type: ignore[assignment]

    # 1. Lexical.
    bm25_all = bm25_search(conn, schema, query, limit=limit)

    # 2. Structural expansion from the lexical anchors.
    structural_ranked = expand_from_anchors(conn, schema, bm25_all, limit=limit)

    # 3. Optional dense candidate generation.
    dense_ranked = dense.search(query, limit=limit) if dense is not None else []

    # 4. Rules signal: annotate + score every candidate on the declared dims.
    candidates = _merge_unique(bm25_all + structural_ranked + dense_ranked)
    ctx = {"anchor_clusters": anchor_clusters(schema, bm25_all)}
    for item in candidates:
        _annotate(item, schema)
        scores = score_item(item, query, dims, ctx)
        item["dimensions"] = scores
        item["rules_score"] = score_summary(scores)
    rules_ranked = sorted(candidates, key=lambda x: x["rules_score"], reverse=True)

    # 5. Fuse. Weights map (bm25, structural, rules, dense) per query intent.
    result = _intent.classify_intent(query) if use_intent else _intent.IntentResult(
        "uniform", 1.0, _intent.UNIFORM
    )
    lists = [bm25_all, structural_ranked, rules_ranked, dense_ranked]
    if weights is not None:
        # A caller stating all four numbers has decided the ratio; do not adjust it.
        fusion_weights = tuple(weights)
        result = _intent.IntentResult("declared", 1.0, fusion_weights)
    else:
        # An absent leg's declared weight is REALLOCATED, not lost. Without this, a profile
        # that leans on dense silently becomes a profile that leans on structural whenever
        # no embedder is configured — a ratio nobody declared. See intent.reallocate_absent.
        fusion_weights = _intent.reallocate_absent(
            result.weights, tuple(bool(lst) for lst in lists)
        )
    weights = fusion_weights
    total = len(candidates)

    if total < schema.small_corpus_threshold:
        fused = rrf_fuse(lists, weights=weights, labels=_LIST_LABELS)
        mode = "rrf+dense" if dense_ranked else "rrf"
    else:
        fused = _weighted_sum(lists, weights)
        mode = "weighted+dense" if dense_ranked else "weighted"

    # 6. Re-attach the rules fields (RRF's slot may have come from a source list
    # that didn't carry them) and trim.
    by_key = {(str(c.get("table")), str(c.get("id"))): c for c in candidates}
    for hit in fused:
        enriched = by_key.get((str(hit.get("table")), str(hit.get("id"))))
        if enriched:
            hit.setdefault("dimensions", enriched.get("dimensions", {}))
            hit.setdefault("rules_score", enriched.get("rules_score", 0.0))
            if "dense_score" in enriched:
                hit.setdefault("dense_score", enriched["dense_score"])

    hits = limit_and_dedupe(fused, limit)
    return HybridResult(
        query=query, total_candidates=total, mode=mode,
        intent=result.intent, hits=hits,
    )


def _annotate(item: dict[str, Any], schema: CorpusSchema) -> None:
    """Populate the reserved ``_text`` / ``_tags`` fields the scorer reads, from
    whatever columns this item's source declared."""
    table = item.get("table")
    try:
        src = schema.source(str(table))
    except KeyError:
        return
    text_parts = [str(item[c]) for c in src.text_columns if item.get(c)]
    item["_text"] = " ".join(text_parts)
    tags: list[str] = []
    for c in src.tag_columns:
        tags.extend(_as_tags(item.get(c)))
    if tags:
        item["_tags"] = tags


def _as_tags(raw: Any) -> list[str]:
    import json
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    try:
        parsed = json.loads(str(raw))
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _merge_unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De-dupe by (table, id), merging any fields the duplicate carried that the
    first copy lacked (so a row found by both BM25 and structural keeps both)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = (str(it.get("table")), str(it.get("id")))
        if key in seen:
            for existing in out:
                if (str(existing.get("table")), str(existing.get("id"))) == key:
                    for k, v in it.items():
                        existing.setdefault(k, v)
                    break
            continue
        seen.add(key)
        out.append(dict(it))
    return out


def _weighted_sum(
    lists: list[list[dict[str, Any]]],
    weights: _intent.Weights,
) -> list[dict[str, Any]]:
    """Weighted reciprocal-rank sum — the large-corpus fusion path.

    Same shape as RRF but without the smoothing constant: a cheaper linear blend
    once the candidate pool is big enough that rank stability matters less.
    """
    acc: dict[tuple[str, str], dict[str, Any]] = {}

    def key(item: dict[str, Any]) -> tuple[str, str]:
        return (str(item.get("table")), str(item.get("id")))

    for lst, w in zip(lists, weights):
        if w == 0:
            continue
        for r, it in enumerate(lst):
            slot = acc.setdefault(key(it), {**it, "weighted_score": 0.0})
            slot["weighted_score"] += w * (1.0 / (r + 1))
    return sorted(acc.values(), key=lambda x: x["weighted_score"], reverse=True)
