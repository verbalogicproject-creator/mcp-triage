"""The optional semantic booster — and the promise that it stays optional.

The load-bearing test here is the degradation one. Every other property is about
dense making results *better*; that one is about the tool still working at all
when the model server is down, numpy is absent, or the feature was never
switched on. A booster that can break the floor is not a booster.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dense
import triage_rank

# numpy is genuinely optional here, so the suite must run without it. Tests that
# assert an index gets BUILT need it; tests that assert we degrade to None do
# not — and those are the ones that matter most in a bare environment.
try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except Exception:  # pragma: no cover - depends on the environment
    HAVE_NUMPY = False

requires_numpy = pytest.mark.skipif(
    not HAVE_NUMPY, reason="numpy absent — dense is optional, so index-building is skipped")


def _row(id_, name, desc, plugin="p@m"):
    return {"id": id_, "name": name, "kind": "skill", "scope": "plugin",
            "plugin": plugin, "enabled": 1, "state_reason": "", "description": desc,
            "body": "", "path": f"/disk/{id_}", "enable_cmd": "", "disable_cmd": "",
            "usage_count": 0, "last_used_at": 0, "root": "/home"}


CORPUS = [
    _row("s1", "kubernetes-deploy", "deploy services to a kubernetes cluster", "a@m"),
    _row("s2", "pytest-debug", "debug failing pytest suites and fixtures", "b@m"),
    _row("s3", "landing-page", "design a marketing landing page", "c@m"),
]


# ── the load-bearing guarantee ──────────────────────────────────────────────

def test_no_dense_index_is_byte_identical_to_lexical_only():
    """dense=None must change nothing at all. This is what lets the feature be
    optional without the default path carrying any risk."""
    a = triage_rank.rank("debug failing tests", CORPUS, limit=5)
    b = triage_rank.rank("debug failing tests", CORPUS, limit=5, dense=None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_dense_is_off_unless_explicitly_configured(monkeypatch):
    """No env var, no dense. The default path must never touch the network."""
    monkeypatch.delenv("MCP_TRIAGE_EMBED_URL", raising=False)
    assert dense.build_dense_index(CORPUS) is None


def test_unreachable_server_degrades_to_none(monkeypatch):
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://127.0.0.1:9/v1/embeddings")
    assert dense.build_dense_index(CORPUS) is None


def test_embedder_returns_none_on_any_failure(monkeypatch):
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://127.0.0.1:9/v1/embeddings")
    assert dense.http_embedder()("some text") is None


def test_embedder_with_no_url_configured_returns_none(monkeypatch):
    monkeypatch.delenv("MCP_TRIAGE_EMBED_URL", raising=False)
    assert dense.http_embedder()("some text") is None


def test_a_dead_embedder_mid_build_yields_no_index(monkeypatch, tmp_path):
    """Half an index is worse than none — it would silently rank part of the
    catalog semantically and the rest not at all."""
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://example.invalid/v1/embeddings")
    calls = {"n": 0}

    def flaky(_text):
        calls["n"] += 1
        return [0.1, 0.2, 0.3] if calls["n"] == 1 else None

    assert dense.build_dense_index(CORPUS, embed=flaky, cache_dir=tmp_path) is None


# ── caching and invalidation ────────────────────────────────────────────────

def test_fingerprint_changes_when_catalog_text_changes():
    a = dense.fingerprint(CORPUS, "u", "m")
    changed = [dict(r) for r in CORPUS]
    changed[0]["description"] = "something else entirely"
    assert dense.fingerprint(changed, "u", "m") != a


def test_fingerprint_changes_when_the_model_changes():
    """Vectors from one model are meaningless to another, so switching models
    must rebuild rather than silently reuse."""
    assert dense.fingerprint(CORPUS, "u", "model-a") != dense.fingerprint(CORPUS, "u", "model-b")


def test_toggling_a_plugin_does_not_invalidate_the_cache():
    """Enabled state is not embedded, so switching plugins on and off — the most
    common thing this tool leads you to do — must stay free."""
    a = dense.fingerprint(CORPUS, "u", "m")
    toggled = [dict(r, enabled=0, state_reason="plugin disabled") for r in CORPUS]
    assert dense.fingerprint(toggled, "u", "m") == a


@requires_numpy
def test_vectors_are_cached_and_reused(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://example.invalid/v1/embeddings")
    calls = {"n": 0}

    def counting(_text):
        calls["n"] += 1
        return [0.1, 0.2, 0.3]

    first = dense.build_dense_index(CORPUS, embed=counting, cache_dir=tmp_path)
    assert first is not None
    after_build = calls["n"]
    assert after_build == len(CORPUS)

    second = dense.build_dense_index(CORPUS, embed=counting, cache_dir=tmp_path)
    assert second is not None
    assert calls["n"] == after_build, "second build must reuse the cache, not re-embed"


@requires_numpy
def test_stale_cache_files_are_pruned(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://example.invalid/v1/embeddings")
    stale = tmp_path / "deadbeef.npz"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"not really an npz")
    dense.build_dense_index(CORPUS, embed=lambda _t: [0.1, 0.2, 0.3], cache_dir=tmp_path)
    assert not stale.exists(), "superseded cache entries must not accumulate"


@requires_numpy
def test_unwritable_cache_dir_still_returns_an_index(monkeypatch, tmp_path):
    """A cache that cannot be written costs a rebuild next run — it must never
    cost an answer."""
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://example.invalid/v1/embeddings")
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    idx = dense.build_dense_index(CORPUS, embed=lambda _t: [0.1, 0.2, 0.3],
                                  cache_dir=blocker)
    assert idx is not None


# ── status reporting ────────────────────────────────────────────────────────

def test_status_says_lexical_only_when_unconfigured(monkeypatch):
    monkeypatch.delenv("MCP_TRIAGE_EMBED_URL", raising=False)
    assert "lexical only" in dense.dense_status()


def test_status_names_the_endpoint_when_configured(monkeypatch):
    monkeypatch.setenv("MCP_TRIAGE_EMBED_URL", "http://127.0.0.1:8145/v1/embeddings")
    assert "8145" in dense.dense_status()
