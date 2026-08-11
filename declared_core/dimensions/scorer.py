"""Rule-based dimension scorer — deterministic, explainable, no ML.

Each dimension has a ``score_*(item, query, ctx) -> float in [0, 1]`` function.
The built-ins cover the 12 dimensions in ``schema.DEFAULT``. Register your own
for a custom schema with ``register(name, fn)``. A declared dimension with no
registered scorer scores a neutral ``0.5`` (no signal either way) rather than
crashing — so you can declare dimensions before you implement their rules.

Reserved item fields (populated by the fusion layer, but you can set them
yourself): ``_text`` is the concatenated searchable text, ``_tags`` is the
merged list of tag strings. Scorers prefer these so they work on *any* declared
column names, not just the ones this file happened to grow up with.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from .schema import DEFAULT, DimensionSchema

Scorer = Callable[[dict[str, Any], str, dict[str, Any]], float]

_WORD_RE = re.compile(r"\w+")


# ── text helpers ─────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _query_terms(query: str) -> list[str]:
    return [t for t in _tokenize(query) if len(t) >= 2]


def _content(item: dict[str, Any]) -> str:
    """The item's searchable text. Prefers the fusion-populated ``_text``; falls
    back to a set of conventional column names for standalone use."""
    if item.get("_text"):
        return str(item["_text"])
    parts = [str(item[k]) for k in ("content", "claim", "purpose", "title", "text")
             if item.get(k)]
    return " ".join(parts)


def _tags(item: dict[str, Any]) -> list[str]:
    raw = item.get("_tags", item.get("tags"))
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    try:
        parsed = json.loads(raw)
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ── Navigation ───────────────────────────────────────────────────────────────

def score_spatial_relevance(item: dict[str, Any], query: str, _ctx: dict[str, Any]) -> float:
    terms = set(_query_terms(query))
    tags = set(_tokenize(" ".join(_tags(item))))
    if not terms:
        return 0.0
    return min(1.0, len(terms & tags) / max(1, len(terms)))


def score_hop_distance(item: dict[str, Any], _query: str, ctx: dict[str, Any]) -> float:
    # ctx["anchor_clusters"] maps a cluster column → the set of values seen in
    # the query anchors. An item sharing an anchor's cluster value is "closer".
    anchor_clusters: dict[str, set] = ctx.get("anchor_clusters") or {}
    for col, values in anchor_clusters.items():
        if item.get(col) in values:
            return 1.0
    return 0.5  # neutral without cluster context


def score_traversal_frequency(_item: dict[str, Any], _query: str, ctx: dict[str, Any]) -> float:
    return float(ctx.get("traversal", {}).get(_item_id(_item), 0.5)) if ctx.get("traversal") else 0.5


def _item_id(item: dict[str, Any]) -> Any:
    return (item.get("table"), item.get("id"))


# ── Retrieval ────────────────────────────────────────────────────────────────

def score_match_precision(item: dict[str, Any], query: str, _ctx: dict[str, Any]) -> float:
    q_terms = _query_terms(query)
    if not q_terms:
        return 0.0
    content_tokens = set(_tokenize(_content(item)))
    hits = sum(1 for t in q_terms if t in content_tokens)
    return hits / len(q_terms)


def score_semantic_coverage(item: dict[str, Any], query: str, _ctx: dict[str, Any]) -> float:
    q_terms = set(_query_terms(query))
    if not q_terms:
        return 0.0
    content_tokens = set(_tokenize(_content(item)))
    if not content_tokens:
        return 0.0
    return len(q_terms & content_tokens) / max(1, len(q_terms | content_tokens))


def score_keyword_hit_rate(item: dict[str, Any], query: str, _ctx: dict[str, Any]) -> float:
    q_terms = _query_terms(query)
    if not q_terms:
        return 0.0
    tokens = _tokenize(_content(item))
    if not tokens:
        return 0.0
    q_set = set(q_terms)
    hits = sum(1 for t in tokens if t in q_set)
    return min(1.0, hits / (len(tokens) / 100.0 + 1))


# ── Synthesis ────────────────────────────────────────────────────────────────

def score_synthesis_potential(item: dict[str, Any], _query: str, _ctx: dict[str, Any]) -> float:
    if item.get("claim") and item.get("reason"):
        return 1.0
    if item.get("claim"):
        return 0.7
    content = _content(item)
    if not content:
        return 0.1
    cues = ("because", "so that", "connects to", "→", "see also", "vs")
    hits = sum(1 for cue in cues if cue in content.lower())
    return min(1.0, 0.4 + hits * 0.15)


def score_generative_scope(item: dict[str, Any], _query: str, _ctx: dict[str, Any]) -> float:
    n_tokens = len(_tokenize(_content(item)))
    if n_tokens <= 0:
        return 0.0
    return min(1.0, math.log10(n_tokens + 1) / 3.0)


# ── Performance ──────────────────────────────────────────────────────────────

def score_latency_class(item: dict[str, Any], _query: str, _ctx: dict[str, Any]) -> float:
    # A pointer/path row is the cheapest to read; a full content row costs a
    # little more. Everything here is a single local row read regardless.
    return 1.0 if item.get("path") else 0.9


def score_storage_tier(item: dict[str, Any], _query: str, _ctx: dict[str, Any]) -> float:
    # Crystallised claims outrank raw content outrank bare pointers.
    if item.get("claim"):
        return 1.0
    if _content(item):
        return 0.8
    return 0.5


# ── Architecture ─────────────────────────────────────────────────────────────

def score_modularity(item: dict[str, Any], _query: str, _ctx: dict[str, Any]) -> float:
    tags = _tags(item)
    n_tokens = len(_tokenize(_content(item)))
    tag_score = min(1.0, len(tags) / 3.0)
    size_score = 1.0 if 5 <= n_tokens <= 200 else 0.4
    return (tag_score + size_score) / 2.0


def score_error_recovery(item: dict[str, Any], _query: str, _ctx: dict[str, Any]) -> float:
    kind = str(item.get("kind", "")).lower()
    if kind in ("gotcha", "invariant"):
        return 1.0
    content = _content(item).lower()
    cues = ("failure", "error", "bug", "recover", "fallback", "retry", "guard", "invariant")
    return min(1.0, sum(1 for c in cues if c in content) / 3.0)


# ── Registry ─────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Scorer] = {
    "spatial_relevance": score_spatial_relevance,
    "hop_distance": score_hop_distance,
    "traversal_frequency": score_traversal_frequency,
    "match_precision": score_match_precision,
    "semantic_coverage": score_semantic_coverage,
    "keyword_hit_rate": score_keyword_hit_rate,
    "synthesis_potential": score_synthesis_potential,
    "generative_scope": score_generative_scope,
    "latency_class": score_latency_class,
    "storage_tier": score_storage_tier,
    "modularity": score_modularity,
    "error_recovery": score_error_recovery,
}

NEUTRAL = 0.5  # score for a declared dimension with no registered scorer


def register(name: str, fn: Scorer) -> None:
    """Register (or override) the scorer for a dimension name."""
    _REGISTRY[name] = fn


def has_scorer(name: str) -> bool:
    return name in _REGISTRY


def score_item(
    item: dict[str, Any],
    query: str,
    schema: DimensionSchema = DEFAULT,
    ctx: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Score ``item`` against ``query`` across every dimension in ``schema``.

    Unregistered dimensions score ``NEUTRAL`` (0.5). Returns ``{name: float}``.
    """
    ctx = ctx or {}
    out: dict[str, float] = {}
    for name in schema.names:
        fn = _REGISTRY.get(name)
        out[name] = round(fn(item, query, ctx), 4) if fn else NEUTRAL
    return out


def score_summary(scores: dict[str, float]) -> float:
    """Collapse a score dict into a single [0, 1] value for one-line ranking."""
    if not scores:
        return 0.0
    return round(sum(scores.values()) / len(scores), 4)
