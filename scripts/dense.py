#!/usr/bin/env python3
"""Optional semantic booster: embed the catalog once, reuse it every query.

WHY THIS IS OPTIONAL AND WHY IT STAYS THAT WAY. Lexical search cannot bridge a
vocabulary gap. "Stop the assistant inventing things about my code" shares no
words with "fact-check the checkable claims against your real files", so BM25
ranked that answer 10th. Embeddings put it 1st. That is the entire reason this
file exists.

It is still a booster, never a floor. Everything here returns None on any
failure — no server, no numpy, a timeout, a malformed response — and a None
dense index makes `hybrid_query` produce results byte-identical to pure lexical
search. Nothing in the default path imports numpy or touches the network.

WHY A BI-ENCODER AND NOT A CROSS-ENCODER RERANKER. A reranker scores every
(query, document) pair at query time, so cost scales with the candidate pool and
nothing can be precomputed; measured on this catalog it also produced no net
ranking gain, because short metadata descriptions collapse into a narrow score
band where ordering is noise. An embedder runs over each document ONCE. The
catalog changes only when you install or toggle something, so the expensive half
is cached and query time is one embedding plus a matmul.

Read-only with respect to Claude Code config; the only thing it writes is its
own vector cache. Stdlib apart from numpy, which is imported lazily.

Env:
    MCP_TRIAGE_EMBED_URL    OpenAI-style /v1/embeddings endpoint. UNSET => dense
                            off entirely, which is the default.
    MCP_TRIAGE_EMBED_MODEL  Model name, when the server wants one.
    MCP_TRIAGE_EMBED_CACHE  Cache directory (default ~/.cache/mcp-triage).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

Embedder = Callable[[str], "list[float] | None"]

DEFAULT_CACHE = Path(os.path.expanduser("~")) / ".cache" / "mcp-triage"

# Long bodies are truncated before embedding: most embedding models cap out
# around 512-2048 tokens, and the tail of a prompt file is boilerplate anyway.
MAX_EMBED_CHARS = 1200


def http_embedder(url: str | None = None, model: str | None = None,
                  timeout: float = 30.0) -> Embedder:
    """An embedder that POSTs to an OpenAI-style `/v1/embeddings` endpoint.

    Returns None on *any* failure rather than raising. A triage run must never
    fail because an optional local model server happens to be down.
    """
    url = url or os.environ.get("MCP_TRIAGE_EMBED_URL", "")
    model = model or os.environ.get("MCP_TRIAGE_EMBED_MODEL", "")

    def embed(text: str) -> "list[float] | None":
        if not url:
            return None
        payload: dict[str, Any] = {"input": [(text or " ")[:MAX_EMBED_CHARS]]}
        if model:
            payload["model"] = model
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            vec = data["data"][0]["embedding"]
            return vec if isinstance(vec, list) and vec else None
        except Exception:
            return None

    return embed


def _doc_text(row: dict) -> str:
    """What gets embedded for one extension.

    Name and description carry the signal; the body adds vocabulary that a terse
    one-line description lacks. Measured, the official per-model query/document
    prefixes made no aggregate difference on this corpus, so plain text is used —
    it keeps the cache portable across embedding models.
    """
    return f"{row.get('name','')} — {row.get('description','')} {row.get('body','')}"


def fingerprint(rows: Sequence[dict], url: str, model: str) -> str:
    """Identity of a cache entry: the corpus text plus who embedded it.

    Covers exactly what changes the vectors. Install, remove, or upgrade an
    extension and the text moves, so the fingerprint moves and the cache is
    rebuilt. Toggling a plugin on or off does NOT change it — enabled state is
    not embedded — so flipping plugins stays free.
    """
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{url}\x00{model}\x00".encode("utf-8"))
    for r in sorted(rows, key=lambda r: r.get("id", "")):
        h.update(r.get("id", "").encode("utf-8"))
        h.update(b"\x00")
        h.update(_doc_text(r).encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def _cache_dir() -> Path:
    return Path(os.environ.get("MCP_TRIAGE_EMBED_CACHE", str(DEFAULT_CACHE)))


def build_dense_index(rows: Sequence[dict], embed: Embedder | None = None,
                      cache_dir: Path | None = None) -> Any | None:
    """A `declared_core` dense index over the catalog, or None if unavailable.

    None is a normal outcome, not an error: it is what happens when the feature
    is switched off, numpy is missing, or the server is down. Callers pass the
    result straight to `hybrid_query(dense=...)`, which treats None as
    "lexical only".
    """
    url = os.environ.get("MCP_TRIAGE_EMBED_URL", "")
    if not url or not rows:
        return None
    try:
        import numpy as np
        from declared_core import NumpyVectorIndex
    except Exception:
        return None

    model = os.environ.get("MCP_TRIAGE_EMBED_MODEL", "")
    embed = embed or http_embedder(url, model)
    fp = fingerprint(rows, url, model)
    cdir = Path(cache_dir) if cache_dir else _cache_dir()
    cache = cdir / f"{fp}.npz"

    mat = None
    if cache.is_file():
        try:
            with np.load(cache) as z:
                cached = z["vectors"]
                cached_ids = list(z["ids"])
            if len(cached_ids) == len(rows):
                mat = cached
        except Exception:
            mat = None

    if mat is None:
        vecs, kept = [], []
        for r in rows:
            v = embed(_doc_text(r))
            if v is None:
                return None  # server went away mid-build; degrade rather than half-index
            vecs.append(v)
            kept.append(r.get("id", ""))
        if not vecs:
            return None
        mat = np.asarray(vecs, dtype=np.float32)
        mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        _write_cache(cdir, cache, mat, kept, np)

    idx = NumpyVectorIndex(embed)
    idx._items = [{"table": "extensions", "id": r.get("id", ""),
                   "_text": r.get("name", "")} for r in rows]
    idx._mat = mat
    idx._dim = int(mat.shape[1])
    return idx


def _write_cache(cdir: Path, cache: Path, mat: Any, ids: list[str], np: Any) -> None:
    """Persist vectors, and drop superseded ones so the cache can't grow forever.

    Best-effort: an unwritable cache directory costs a rebuild next run, which is
    a far better failure than refusing to answer.
    """
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        # Write through a handle: np.savez APPENDS ".npz" to a path that lacks
        # it, which would silently produce "<name>.npz.tmp.npz" and leave the
        # move below with nothing to rename.
        with open(tmp, "wb") as fh:
            np.savez(fh, vectors=mat, ids=np.array(ids))
        shutil.move(str(tmp), str(cache))
        for old in cdir.glob("*.npz"):
            if old != cache:
                old.unlink(missing_ok=True)
    except Exception:
        pass


def dense_status() -> str:
    """One line for the human, so 'is the booster on?' is never a guess."""
    url = os.environ.get("MCP_TRIAGE_EMBED_URL", "")
    if not url:
        return "lexical only (set MCP_TRIAGE_EMBED_URL to add semantic matching)"
    try:
        import numpy  # noqa: F401
    except Exception:
        return f"lexical only (numpy missing; {url} configured but unusable)"
    return f"lexical + semantic via {url}"
