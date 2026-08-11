"""Declared dimensions — named-attribute embeddings, not neural vectors.

A dimension is a named, interpretable [0, 1] attribute of an item ("how
precisely does it match the query terms", "how self-contained is it"). Together
they form the *rules signal*: a deterministic, explainable ranker with no model,
no training, no API. You can read every score and know exactly why it is what it
is.

`DEFAULT` is a small, domain-agnostic 12-dimension schema that works on any text
corpus out of the box. It is a starting palette, not a ceiling — declare your
own `DimensionSchema` and register scorers for it (see ``scorer.register``) when
your domain has structure worth measuring (visual style, physics role, cost
tier, …). Keep the schema *curated*: dimensions you never score add noise, not
signal. The proven small schema beats the impressive large one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionDef:
    """One declared dimension.

    name        stable identifier, used as the key in the score dict.
    group       coarse grouping, for display/filtering (navigation, retrieval…).
    description free-text explanation of what a high score means.
    higher_is   'better' | 'closer' — semantics of a larger value.
    weight      default contribution when the dims are summarised. Tunable.
    """

    name: str
    group: str
    description: str
    higher_is: str = "better"
    weight: float = 1.0


@dataclass(frozen=True)
class DimensionSchema:
    """An ordered, unique collection of `DimensionDef`s."""

    dims: tuple[DimensionDef, ...]

    def __post_init__(self) -> None:
        if not self.dims:
            raise ValueError("DimensionSchema needs at least one dimension")
        names = [d.name for d in self.dims]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate dimension names: {names}")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.dims)

    def __iter__(self):
        return iter(self.dims)

    def __len__(self) -> int:
        return len(self.dims)


# ── The built-in, domain-agnostic default (12 dims) ──────────────────────────
# These operate on any text item. They are the "meta-level" dimensions: they
# measure the *retrieval relationship* between item and query, not domain
# content. Domain palettes (visual/physics/media/…) belong in the consumer that
# has that domain — declare + register them there.
DEFAULT = DimensionSchema((
    # ── Navigation ──────────────────────────────────────────────────────────
    DimensionDef("spatial_relevance", "navigation",
                 "Overlap between the item's tags and the query focus."),
    DimensionDef("hop_distance", "navigation",
                 "Structural closeness to the query anchors (shared cluster value). "
                 "Neutral without cluster context.", higher_is="closer"),
    DimensionDef("traversal_frequency", "navigation",
                 "How often prior recall paths crossed this item. Neutral until "
                 "traversal is logged."),
    # ── Retrieval ───────────────────────────────────────────────────────────
    DimensionDef("match_precision", "retrieval",
                 "Fraction of query terms literally present in the item text."),
    DimensionDef("semantic_coverage", "retrieval",
                 "Jaccard overlap of query terms and item terms."),
    DimensionDef("keyword_hit_rate", "retrieval",
                 "Density of query keywords per ~100 tokens of item text."),
    # ── Synthesis ───────────────────────────────────────────────────────────
    DimensionDef("synthesis_potential", "synthesis",
                 "Presence of structured claim+reason or cross-reference cues."),
    DimensionDef("generative_scope", "synthesis",
                 "Breadth — how many entities/how much ground the item covers."),
    # ── Performance ─────────────────────────────────────────────────────────
    DimensionDef("latency_class", "performance",
                 "Retrieval cost class. Higher = cheaper to read.",),
    DimensionDef("storage_tier", "performance",
                 "How authoritative/durable the source is. Higher = more durable."),
    # ── Architecture ────────────────────────────────────────────────────────
    DimensionDef("modularity", "architecture",
                 "Whether the item factors cleanly (tagged, bounded in size)."),
    DimensionDef("error_recovery", "architecture",
                 "Presence of failure-mode reasoning — gotchas, guards, retros."),
))
