"""Reciprocal Rank Fusion, with optional per-list weights.

For small, mixed-signal corpora RRF beats weighted score-sums because BM25
scores, cosine similarities, and rules scores live on different value ranges.
RRF uses ONLY rank:

    fused(item) = Σ_j  w_j / (k + rank_j(item))

where j iterates the input ranked lists, ``rank_j`` is 1-based, ``k`` smooths
(60 is the standard from Cormack et al. 2009), and ``w_j`` is a per-list weight.

With all weights equal this is classic RRF. `declared_core` uses the weights to
make fusion **intent-adaptive**: an ``exact_match`` query up-weights the BM25
list; an ``exploratory`` query up-weights the structural and dense lists. See
`intent.py`.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

DEFAULT_K = 60


def rrf_fuse(
    ranked_lists: Sequence[list[dict[str, Any]]],
    *,
    weights: Sequence[float] | None = None,
    k: int = DEFAULT_K,
    labels: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge ranked lists by (optionally weighted) reciprocal-rank fusion.

    ranked_lists  list of ranked lists, position 0 = best.
    weights       one weight per list (defaults to all 1.0). A weight of 0
                  drops that list from the fusion entirely.
    labels        one provenance label per list (e.g. ``"bm25"``, ``"rules"``).
                  Authoritative for ``rrf_sources``; when omitted, the label is
                  sniffed from each item's fields (back-compat for direct
                  callers). See ``_source_label`` for why the caller-declared
                  label matters.
    Returns one ranked list; each item gains ``rrf_score`` + ``rrf_sources``.
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights length must match ranked_lists length")
    if labels is not None and len(labels) != len(ranked_lists):
        raise ValueError("labels length must match ranked_lists length")

    fused: dict[Any, dict[str, Any]] = {}
    for li, (lst, w) in enumerate(zip(ranked_lists, weights)):
        if w == 0:
            continue
        role = labels[li] if labels is not None else None
        for rank_zero_based, item in enumerate(lst):
            key = _key_of(item)
            if key is None:
                continue
            slot = fused.get(key)
            if slot is None:
                slot = {**item, "rrf_score": 0.0, "rrf_sources": []}
                fused[key] = slot
            slot["rrf_score"] += w / (k + rank_zero_based + 1)
            slot["rrf_sources"].append(_source_label(item, role))
    return sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)


def _key_of(item: dict[str, Any]) -> Any:
    """Prefer (table, id) to avoid cross-source id collisions."""
    ident = item.get("id")
    table = item.get("table")
    if table is not None and ident is not None:
        return (table, ident)
    return ident


def _source_label(item: dict[str, Any], role: str | None = None) -> str:
    """Provenance label for one list-contribution to a fused hit.

    ``role`` (the fused list's declared signal, supplied by ``hybrid_query``) is
    authoritative. It has to be: the rules list is fed *merged candidate copies*
    that still hold the ``bm25_rank`` / ``structural_hop`` marker of whichever
    signal first retrieved the row, so sniffing item fields would relabel every
    rules contribution as bm25/structural — duplicating a label already recorded
    and hiding the ``rules`` signal from ``rrf_sources`` entirely. Structural
    keeps its per-item hop detail (``structural:kind`` / ``:tag`` / ``:link:*``).
    With no role (a direct ``rrf_fuse`` caller), fall back to field-sniffing.
    """
    if role == "structural":
        hop = item.get("structural_hop")
        return f"structural:{hop}" if hop else "structural"
    if role:
        return role
    if "bm25_rank" in item:
        return "bm25"
    if "structural_hop" in item:
        return f"structural:{item['structural_hop']}"
    if "dense_score" in item:
        return "dense"
    if "rules_score" in item:
        return "rules"
    return "unknown"


def limit_and_dedupe(items: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Take up to ``limit`` items preserving order, no duplicates."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = _key_of(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out
