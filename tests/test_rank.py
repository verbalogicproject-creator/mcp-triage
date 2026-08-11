"""Ranking: does it retrieve real extensions, reproducibly, with usage counted?

The property that matters most is the first one — a suggestion must correspond
to something that was actually found on disk. Everything else (determinism,
usage weighting, the sibling hop) is about the answer being *useful*; that one is
about it being *true*.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import triage_rank


def _row(id_, name, kind="skill", plugin="p@m", enabled=1, description="",
         body="", usage=0, root="/home"):
    return {
        "id": id_, "name": name, "kind": kind, "scope": "plugin", "plugin": plugin,
        "enabled": enabled, "state_reason": "", "description": description,
        "body": body, "path": f"/disk/{id_}", "enable_cmd": f"enable {plugin}",
        "disable_cmd": f"disable {plugin}", "usage_count": usage,
        "last_used_at": 0, "root": root,
    }


CORPUS = [
    _row("s1", "kubernetes-deploy", description="deploy services to a kubernetes cluster"),
    _row("s2", "pytest-debug", description="debug failing pytest suites and fixtures"),
    _row("s3", "landing-page", description="design a marketing landing page"),
    _row("s4", "sql-tuning", description="tune slow sql queries and indexes"),
]


# ── the load-bearing property ───────────────────────────────────────────────

def test_every_hit_corresponds_to_a_real_catalog_row():
    """Nothing may be suggested that was not probed from disk. This is the whole
    reason ranking runs over an index instead of model recall."""
    hits = triage_rank.rank("debug a failing test suite", CORPUS, limit=10)
    known = {r["id"] for r in CORPUS}
    assert hits, "expected at least one hit"
    assert all(h["id"] in known for h in hits)


def test_hits_carry_the_path_they_came_from():
    """A suggestion you can't open is a suggestion you can't check."""
    hits = triage_rank.rank("kubernetes cluster", CORPUS, limit=5)
    assert all(h["path"].startswith("/disk/") for h in hits)


def test_relevant_row_outranks_irrelevant_one():
    hits = triage_rank.rank("slow sql query index", CORPUS, limit=4)
    assert hits[0]["id"] == "s4"


def test_ranking_is_deterministic():
    a = triage_rank.rank("design a landing page", CORPUS, limit=5)
    b = triage_rank.rank("design a landing page", CORPUS, limit=5)
    assert [h["id"] for h in a] == [h["id"] for h in b]
    assert [h["rules_score"] for h in a] == [h["rules_score"] for h in b]


def test_empty_query_or_empty_corpus_returns_nothing():
    assert triage_rank.rank("", CORPUS) == []
    assert triage_rank.rank("   ", CORPUS) == []
    assert triage_rank.rank("anything", []) == []


def test_hits_report_which_signals_matched():
    hits = triage_rank.rank("pytest fixtures", CORPUS, limit=5)
    assert hits[0]["matched_by"], "a hit should say why it surfaced"


# ── usage as a declared dimension ───────────────────────────────────────────

def test_proven_score_is_zero_without_recorded_use():
    assert triage_rank._proven_score({"usage_count": 0}, "q", {}) == 0.0
    assert triage_rank._proven_score({}, "q", {}) == 0.0


def test_proven_score_rises_with_use_and_saturates():
    s1 = triage_rank._proven_score({"usage_count": 1}, "q", {})
    s10 = triage_rank._proven_score({"usage_count": 10}, "q", {})
    s900 = triage_rank._proven_score({"usage_count": 900}, "q", {})
    s1000 = triage_rank._proven_score({"usage_count": 1000}, "q", {})
    assert 0 < s1 < s10 < s900 <= s1000 <= 1.0
    # Compressive, as documented: going from never-used to used-once says far
    # more than going from 900 uses to 1000.
    assert (s1 - 0.0) > (s1000 - s900)


def test_proven_score_ignores_the_clock():
    """No wall-clock in the retrieval path — the engine requires reproducibility,
    so recency is deliberately not a signal here."""
    item = {"usage_count": 5, "last_used_at": 0}
    assert triage_rank._proven_score(item, "q", {}) == \
        triage_rank._proven_score({"usage_count": 5, "last_used_at": 99999}, "q", {})


def test_used_extension_outranks_identical_unused_one():
    """The declared dimension alone cannot achieve this — measured, it moves a
    hit by well under one position, because it is one of thirteen dims inside
    one of four rank-fused lists. The bounded lift is what makes the promise
    ('your proven tools rank above ones you've never touched') actually true."""
    corpus = [
        _row("cold", "widget-tool", plugin="a@m", description="work with widgets", usage=0),
        _row("warm", "widget-tool", plugin="b@m", description="work with widgets", usage=500),
    ]
    hits = triage_rank.rank("widgets", corpus, limit=2)
    assert [h["id"] for h in hits][0] == "warm"


def test_usage_cannot_hijack_an_unrelated_query():
    """A prior may reorder what matched; it may never manufacture relevance."""
    corpus = [
        _row("relevant", "sql-tuning", plugin="a@m", usage=0,
             description="tune slow sql queries and indexes"),
        _row("popular", "landing-page", plugin="b@m", usage=14000,
             description="design a marketing landing page"),
    ]
    ids = [h["id"] for h in triage_rank.rank("slow sql query index", corpus, limit=5)]
    assert ids[0] == "relevant"


def test_lift_never_introduces_something_retrieval_did_not_return():
    """The lift reorders the retrieved pool. If BM25 never surfaced it, no amount
    of past usage may put it on screen."""
    corpus = [
        _row("match", "alpha-tool", plugin="a@m", usage=0, description="alpha topic here"),
        _row("popular", "beta-tool", plugin="b@m", usage=99999,
             description="completely unrelated subject matter"),
    ]
    ids = [h["id"] for h in triage_rank.rank("alpha topic", corpus, limit=5)]
    assert "popular" not in ids


def test_lift_is_bounded_to_its_declared_positions():
    """A heavily-used item climbs by at most PROVEN_LIFT_POSITIONS places, so a
    strong textual match cannot be buried under a popular weak one."""
    hits = [{"usage_count": 0} for _ in range(10)]
    hits[9]["usage_count"] = 10 ** 9  # proven score saturates at 1.0
    lifted = triage_rank._apply_proven_lift(hits)
    moved_to = lifted.index(hits[9])
    # It climbs, but never past the declared bound. (It lands just shy of the
    # full lift because ties resolve in favour of the engine's original order.)
    assert moved_to < 9
    assert moved_to >= 9 - int(triage_rank.PROVEN_LIFT_POSITIONS)


def test_lift_is_a_stable_reordering():
    hits = [{"usage_count": 0} for _ in range(5)]
    assert triage_rank._apply_proven_lift(hits) == hits


# ── the sibling hop ─────────────────────────────────────────────────────────

def test_matching_one_skill_surfaces_its_plugin_mates():
    """You enable a plugin, not a skill, so a match should pull in what comes
    with it."""
    corpus = [
        _row("a1", "orb-mount", plugin="voice@mkt", description="mount the voice orb"),
        _row("a2", "cartridge", plugin="voice@mkt", description="author a persona cartridge"),
        _row("z1", "unrelated", plugin="other@mkt", description="totally different topic"),
    ]
    ids = [h["id"] for h in triage_rank.rank("mount the voice orb", corpus, limit=5)]
    assert "a1" in ids
    assert "a2" in ids, "plugin-mate should ride along via the structural hop"


def test_non_plugin_rows_do_not_cluster_together():
    """Rows with no plugin must not become each other's neighbours just because
    both have an empty plugin field."""
    corpus = [
        _row("m1", "alpha-server", kind="mcp", plugin="", description="alpha topic"),
        _row("m2", "beta-server", kind="mcp", plugin="", description="unrelated beta matter"),
    ]
    ids = [h["id"] for h in triage_rank.rank("alpha topic", corpus, limit=5)]
    assert "m1" in ids and "m2" not in ids


# ── partitioning by install ─────────────────────────────────────────────────

def test_partition_splits_actionable_from_other_install():
    corpus = [
        _row("here_off", "deploy-tool", enabled=0, root="/home",
             description="deploy to kubernetes"),
        _row("here_on", "deploy-helper", enabled=1, root="/home",
             description="deploy to kubernetes"),
        _row("there", "deploy-thing", enabled=1, root="/other",
             description="deploy to kubernetes"),
    ]
    parts = triage_rank.partition("deploy to kubernetes", corpus, limit=10,
                                  current_root="/home")
    assert [h["id"] for h in parts["turn_on"]] == ["here_off"]
    assert [h["id"] for h in parts["already"]] == ["here_on"]
    assert [h["id"] for h in parts["elsewhere"]] == ["there"]


def test_other_install_never_appears_as_actionable():
    """An extension enabled in the other Claude home is unreachable from this
    session; offering it as available or enable-able would be a false claim."""
    corpus = [_row("there", "thing", enabled=1, root="/other", description="alpha topic")]
    parts = triage_rank.partition("alpha topic", corpus, limit=5, current_root="/home")
    assert parts["turn_on"] == [] and parts["already"] == []
    assert len(parts["elsewhere"]) == 1


def test_idle_lists_only_enabled_irrelevant_plugins_and_servers_in_this_root():
    # Distinct plugins on purpose: sharing one would make them plugin-mates, and
    # the structural hop would (correctly) pull the irrelevant ones in as
    # neighbours of the match.
    corpus = [
        _row("p_hit", "match-plugin", kind="plugin", plugin="a@m", enabled=1,
             root="/home", description="alpha topic"),
        _row("p_idle", "idle-plugin", kind="plugin", plugin="b@m", enabled=1,
             root="/home", description="nothing in common"),
        _row("p_off", "off-plugin", kind="plugin", plugin="c@m", enabled=0,
             root="/home", description="nothing in common"),
        _row("p_other", "other-plugin", kind="plugin", plugin="d@m", enabled=1,
             root="/other", description="nothing in common"),
    ]
    idle_ids = {r["id"] for r in triage_rank.partition(
        "alpha topic", corpus, limit=5, current_root="/home")["idle"]}
    assert "p_idle" in idle_ids
    assert "p_hit" not in idle_ids      # it matched
    assert "p_off" not in idle_ids      # already off
    assert "p_other" not in idle_ids    # different install
