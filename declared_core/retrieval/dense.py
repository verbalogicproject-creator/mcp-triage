"""Optional dense (semantic) retrieval — a booster, never a dependency.

Dense retrieval adds paraphrase recall: matches that share *meaning* with the
query but few literal terms. It is strictly optional. If you pass no dense index
to `hybrid_query`, retrieval is pure-lexical and byte-for-byte deterministic. If
you pass one whose embedder is unavailable, it returns ``[]`` and retrieval
*degrades cleanly* to the same lexical result. This is the "AI-optional,
degrades to AI-less" contract.

`declared_core` ships one small implementation, `NumpyVectorIndex` — brute-force
cosine over an in-memory float matrix, good for the thousands-of-rows corpora
this engine targets. Bring your own embedder (any ``str -> sequence[float] |
None`` callable: a local model, an API, a hash — your choice). For large corpora,
implement the `DenseIndex` protocol over your own ANN backend and pass that
instead; the fusion layer only needs ``.search(query, limit)``.

Requires the optional ``numpy`` dependency: ``pip install declared-core[dense]``.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence, runtime_checkable

Embedder = Callable[[str], Sequence[float] | None]


@runtime_checkable
class DenseIndex(Protocol):
    """Any object that turns a query into ranked candidate rows.

    Each returned dict must carry ``table`` + ``id`` (so it fuses with the other
    signals) and a ``dense_score`` float. Return ``[]`` when unavailable.
    """

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]: ...


class NumpyVectorIndex:
    """Brute-force cosine index over an in-memory matrix.

    Build it by embedding a set of item dicts, then query it. Vectors are
    L2-normalised once at build time so search is a single matmul.
    """

    def __init__(self, embed: Embedder) -> None:
        self._embed = embed
        self._items: list[dict[str, Any]] = []
        self._mat = None  # np.ndarray | None
        self._dim: int | None = None

    @classmethod
    def from_items(
        cls,
        embed: Embedder,
        items: Sequence[dict[str, Any]],
        text_of: Callable[[dict[str, Any]], str] = lambda it: str(it.get("_text", "")),
    ) -> "NumpyVectorIndex":
        """Embed each item's text and build the index. Items whose text embeds to
        ``None`` are skipped."""
        idx = cls(embed)
        idx.build(items, text_of)
        return idx

    def build(
        self,
        items: Sequence[dict[str, Any]],
        text_of: Callable[[dict[str, Any]], str] = lambda it: str(it.get("_text", "")),
    ) -> "NumpyVectorIndex":
        import numpy as np

        vecs: list[Any] = []
        kept: list[dict[str, Any]] = []
        dim: int | None = None
        for it in items:
            v = self._embed(text_of(it))
            if v is None:
                continue
            arr = np.asarray(v, dtype=np.float32)
            if arr.ndim != 1 or arr.size == 0:
                continue
            if dim is None:
                dim = int(arr.shape[0])
            if arr.shape[0] != dim:
                continue  # skip ragged vectors — a consistent matrix or nothing
            vecs.append(arr)
            kept.append(dict(it))
        if not vecs:
            self._items, self._mat, self._dim = [], None, None
            return self
        self._dim = dim
        self._mat = _normalize_rows(np.vstack(vecs))
        self._items = kept
        return self

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        if self._mat is None or not self._items:
            return []
        import numpy as np

        qv = self._embed(query)
        if qv is None:
            return []
        q = np.asarray(qv, dtype=np.float32)
        if q.ndim != 1 or q.shape[0] != self._dim:
            return []  # dim mismatch — embedded by a different model
        q = q / (float(np.linalg.norm(q)) or 1.0)
        sims = self._mat @ q
        order = np.argsort(-sims)[:limit]
        out: list[dict[str, Any]] = []
        for i in order:
            item = dict(self._items[int(i)])
            item["dense_score"] = float(sims[int(i)])
            out.append(item)
        return out


def _normalize_rows(m):
    import numpy as np

    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms
