"""Declared dimensions: a deterministic, explainable rules signal.

Dimensions are named-attribute embeddings — interpretable [0, 1] floats, not
neural vectors. `DEFAULT` is a domain-agnostic 12-dim schema; register your own
scorers for a custom schema. See `schema` and `scorer`.
"""

from .schema import DEFAULT, DimensionDef, DimensionSchema
from .scorer import NEUTRAL, has_scorer, register, score_item, score_summary

__all__ = [
    "DEFAULT",
    "DimensionDef",
    "DimensionSchema",
    "NEUTRAL",
    "has_scorer",
    "register",
    "score_item",
    "score_summary",
]
