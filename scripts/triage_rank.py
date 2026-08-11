#!/usr/bin/env python3
"""Rank the extension catalog against a task description.

WHY RETRIEVAL AND NOT JUDGEMENT. The catalog can hold several hundred
extensions across two installs. Pasting that into a session and asking "which
ones fit?" is a recall problem, and recall degrades exactly where it matters —
the long tail nobody remembers installing. So matching runs as retrieval over an
index instead: BM25 over each extension's name, description, and body, scored by
the engine's declared dimensions and fused.

THE PLUGIN IS THE UNIT OF ACTION, NOT THE UNIT OF RELEVANCE. You do not enable a
skill; you enable the plugin that ships it, and its siblings arrive whether you
asked for them or not. So results are GROUPED by plugin when displayed.

Grouping is deliberately a display concern. Feeding that same relationship into
the ranking — as a structural hop over a plugin cluster column — was measurably
worse, and the failure was systematic rather than marginal. Rank fusion combines
lists by position, so an item appearing in both the lexical and the structural
list outscores one appearing in the lexical list alone. A plugin shipping five
components therefore got five double-counted entries while a lean plugin with one
command got one, independent of how well either actually matched. Measured: a
query almost verbatim from `vouch`'s description ranked `/vouch:vouch` FIRST on
BM25 alone and EIGHTH after fusion, displaced by four agents that arrived purely
as each other's neighbours.

Relevance is therefore decided by the best-matching piece. Siblings are shown
because they are what you get, not counted because there are many of them.

USAGE AS A DECLARED SIGNAL. "You have actually used this before" is evidence,
and it belongs in the ranking rather than bolted on afterwards as a multiplier.
It is declared as a `proven` dimension and scored by the engine's rules signal,
so its contribution is visible and tunable like any other. It is count-based,
never clock-based — the engine forbids wall-clock in the retrieval path because
it would make results non-reproducible.

Read-only. Stdlib-only (the vendored engine adds no dependency).

Usage:
    python3 triage_rank.py "add voice to a next.js app"
    python3 triage_rank.py "debug flaky pytest" --json --limit 15
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # vendored engine
sys.path.insert(0, str(Path(__file__).resolve().parent))          # sibling scripts

from declared_core import (  # noqa: E402
    CorpusSchema,
    DEFAULT_DIMENSIONS,
    DimensionDef,
    DimensionSchema,
    SourceTable,
    hybrid_query,
    install_fts,
    register_dimension_scorer,
)

import catalog  # noqa: E402

# ── the declared corpus ─────────────────────────────────────────────────────

COLUMNS = (
    "id", "name", "kind", "scope", "plugin", "group_key", "enabled",
    "state_reason", "description", "body", "path", "enable_cmd", "disable_cmd",
    "usage_count", "last_used_at", "root",
)

EXTENSIONS = SourceTable(
    name="extensions",
    id_column="id",
    # What a task description actually matches against.
    text_columns=("name", "description", "body"),
    carry_columns=(
        "kind", "scope", "plugin", "group_key", "enabled", "state_reason", "path",
        "enable_cmd", "disable_cmd", "usage_count", "last_used_at", "root",
    ),
    # No cluster_columns on purpose — see the module docstring. `group_key` is
    # carried for DISPLAY grouping only; declaring it here as a cluster column
    # would re-create the structural hop that buried genuine matches.
)

# `proven` extends the engine's 12 generic dimensions rather than replacing them.
PROVEN = DimensionDef(
    name="proven",
    group="evidence",
    description="How much real use this extension has on record here. "
                "High means you have reached for it before.",
    higher_is="better",
    weight=1.0,
)

DIMENSIONS = DimensionSchema((*DEFAULT_DIMENSIONS.dims, PROVEN))

SCHEMA = CorpusSchema(sources=(EXTENSIONS,), dimensions=DIMENSIONS)


def _proven_score(item: dict[str, Any], query: str, ctx: dict[str, Any]) -> float:
    """Log-scaled, saturating score for recorded usage.

    Deliberately compressive: the gap between 0 and 1 uses should count for more
    than the gap between 900 and 1000. Deliberately clock-free: recency would
    read the wall clock, and the engine requires a reproducible retrieval path.
    """
    n = int(item.get("usage_count") or 0)
    if n <= 0:
        return 0.0
    return min(1.0, 0.25 + math.log10(n + 1) / 4.0)


register_dimension_scorer("proven", _proven_score)

# How far recorded usage may lift an item, in list positions.
#
# WHY A BOUNDED NUDGE AND NOT JUST THE DIMENSION. `proven` is declared as a
# dimension so its value is visible and tunable, but the dimension alone cannot
# reach the final order: it is one of thirteen dims feeding the rules signal,
# which is in turn one of four *rank*-fused lists. Measured, that path moves a
# heavily-used extension by well under one position — so "your proven tools rank
# above ones you have never touched" was not actually true of the output.
#
# Usage is a prior, not evidence of relevance, so it gets a prior's job: reorder
# things that ALREADY matched, by a bounded amount. It can never introduce an
# extension that retrieval did not surface, which is the property that keeps a
# suggestion honest. Three positions is enough to settle near-ties and lift a
# daily driver over a never-used neighbour, and too small to bury a strong
# textual match under a popular irrelevance.
PROVEN_LIFT_POSITIONS = 3.0


# ── indexing ────────────────────────────────────────────────────────────────

def build_index(rows: list[dict], conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Load catalog rows into an in-memory (by default) SQLite corpus + FTS.

    The index is disposable: it is rebuilt per run from a fresh probe, so it can
    never serve a stale answer about what is installed or switched on.
    """
    conn = conn or sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"{c} TEXT" if c not in ("enabled", "usage_count", "last_used_at")
                     else f"{c} INTEGER" for c in COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS extensions ({cols}, PRIMARY KEY (id))")
    conn.execute("DELETE FROM extensions")
    placeholders = ", ".join("?" for _ in COLUMNS)
    conn.executemany(
        f"INSERT OR REPLACE INTO extensions ({', '.join(COLUMNS)}) VALUES ({placeholders})",
        [tuple(_row_values(r)) for r in rows],
    )
    conn.commit()
    install_fts(conn, SCHEMA)
    return conn


def _row_values(r: dict) -> list[Any]:
    out = []
    for c in COLUMNS:
        if c == "group_key":
            # Rows with no plugin (a lone MCP server, a loose agent) each get
            # their own key so they display alone rather than piling into one
            # bucket keyed on the empty string.
            out.append(r.get("plugin") or f"solo:{r.get('id', '')}")
        else:
            out.append(r.get(c, "" if c not in ("enabled", "usage_count", "last_used_at") else 0))
    return out


# ── ranking ─────────────────────────────────────────────────────────────────

def _apply_proven_lift(hits: list[dict]) -> list[dict]:
    """Reorder retrieved hits by recorded usage, bounded to a few positions.

    Deterministic: a stable sort on (position - lift), so equal keys keep the
    engine's order. Operates only on what retrieval already returned.
    """
    ordered = list(hits)
    keyed = [
        (i - PROVEN_LIFT_POSITIONS * _proven_score(h, "", {}), i, h)
        for i, h in enumerate(ordered)
    ]
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [h for _, _, h in keyed]


def rank(query: str, rows: list[dict], limit: int = 20) -> list[dict]:
    """Extensions most relevant to `query`, best first, with match provenance."""
    if not query.strip() or not rows:
        return []
    conn = build_index(rows)
    # Retrieve a wider pool than we will show, so the usage lift has something to
    # lift *into* the visible window. Lifting after truncation would only reorder
    # rows that already made the cut — the proven tool sitting just below it, the
    # exact case the lift exists for, could never surface.
    result = hybrid_query(query, SCHEMA, conn, limit=limit + int(PROVEN_LIFT_POSITIONS) + 5)
    hits = []
    for h in _apply_proven_lift(result.hits)[:limit]:
        hits.append({
            "id": h.get("id", ""),
            "name": h.get("name", ""),
            "kind": h.get("kind", ""),
            "plugin": h.get("plugin", ""),
            "enabled": int(h.get("enabled") or 0),
            "state_reason": h.get("state_reason", ""),
            "description": h.get("description", ""),
            "path": h.get("path", ""),
            "enable_cmd": h.get("enable_cmd", ""),
            "disable_cmd": h.get("disable_cmd", ""),
            "usage_count": int(h.get("usage_count") or 0),
            "root": h.get("root", ""),
            # What to group this under when displaying: the plugin you would
            # actually switch on, or the item itself when it has no plugin.
            "group_key": h.get("group_key", "") or h.get("name", ""),
            # Provenance: which signals put this here, and its rules score.
            "matched_by": list(h.get("rrf_sources") or []),
            "rules_score": round(float(h.get("rules_score") or 0.0), 4),
        })
    conn.close()
    return hits


def group_hits(hits: list[dict]) -> list[dict]:
    """Collapse hits into per-plugin groups, best-ranked group first.

    A group's position is its BEST member's position, never its size — that is
    the whole correction. Ordering by anything size-derived is what let a large
    plugin's siblings outrank a small plugin's exact match.
    """
    order: list[str] = []
    members: dict[str, list[dict]] = {}
    for h in hits:
        key = h.get("group_key") or h.get("name", "")
        if key not in members:
            members[key] = []
            order.append(key)
        members[key].append(h)
    return [{
        "group_key": k,
        "plugin": members[k][0].get("plugin", ""),
        "enabled": members[k][0].get("enabled", 0),
        "state_reason": members[k][0].get("state_reason", ""),
        "enable_cmd": members[k][0].get("enable_cmd", ""),
        "root": members[k][0].get("root", ""),
        "hits": members[k],
    } for k in order]


def partition(query: str, rows: list[dict], limit: int = 20,
              current_root: str | None = None) -> dict[str, list[dict]]:
    """Both triage directions in one pass, split by which install a hit lives in.

    turn_on   relevant, switched off, and in THIS session's install — actionable.
    already   relevant and already available here — nothing to do.
    idle      available here but irrelevant to this task; disable candidates.
    elsewhere relevant, but installed under a different Claude home.

    `elsewhere` is a separate bucket on purpose. An extension enabled in the
    other install is still unreachable from this session, so folding it into
    `already` would claim a capability that isn't there, and folding it into
    `turn_on` would hand over an enable command that cannot help from here.
    """
    current_root = str(current_root or catalog.HOME)
    hits = rank(query, rows, limit=limit)
    ranked_ids = {h["id"] for h in hits}
    here = [h for h in hits if h["root"] == current_root]
    return {
        "turn_on": [h for h in here if not h["enabled"]],
        "already": [h for h in here if h["enabled"]],
        "elsewhere": [h for h in hits if h["root"] != current_root],
        "idle": [r for r in rows
                 if r.get("enabled") and r["id"] not in ranked_ids
                 and r.get("root") == current_root
                 and r.get("kind") in ("plugin", "mcp")],
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {"--json", "--project", "--home", "--limit"}
    terms = [a for i, a in enumerate(args)
             if not a.startswith("--") and (i == 0 or args[i - 1] not in flags)]
    query = " ".join(terms).strip()
    if not query:
        print("usage: triage_rank.py \"<what you're about to work on>\"", file=sys.stderr)
        return 2

    project = Path(args[args.index("--project") + 1]) if "--project" in args else Path.cwd()
    homes = [Path(args[i + 1]) for i, a in enumerate(args) if a == "--home"] or None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 20

    rows = catalog.build_multi(project, homes)
    # The FIRST root scanned is the one being triaged for; any others are
    # reference. Pinning this to the session's own HOME instead would mark every
    # hit unreachable whenever you deliberately point --home somewhere else.
    primary = str((homes or [catalog.HOME])[0])
    parts = partition(query, rows, limit=limit, current_root=primary)

    if "--json" in args:
        print(json.dumps(parts, indent=2))
        return 0

    print(f"Task: {query}")
    print(f"Searched {len(rows)} extension(s) · this install: {primary}\n")

    if parts["turn_on"]:
        print("Switched off, but relevant — consider turning on:")
        for g in group_hits(parts["turn_on"]):
            label = g["plugin"] or g["hits"][0]["name"]
            extra = f"  (+{len(g['hits']) - 1} more piece(s))" if len(g["hits"]) > 1 else ""
            print(f"\n  {label}{extra}")
            if g["enable_cmd"]:
                print(f"    {g['enable_cmd']}")
            for h in g["hits"]:
                used = f" · used {h['usage_count']}x" if h["usage_count"] else ""
                print(f"      {h['kind']:8s} {h['name']}{used}")
                print(f"               {h['path']}")
    else:
        print("Nothing switched off looks relevant to this task.")

    if parts["already"]:
        print("\nAlready available and relevant:")
        for h in parts["already"]:
            used = f"  (used {h['usage_count']}x)" if h["usage_count"] else ""
            print(f"  {h['kind']:8s} {h['name']}{used}")

    if parts["elsewhere"]:
        print("\nIn your OTHER Claude install — not reachable from this session:")
        for h in parts["elsewhere"]:
            print(f"  {h['kind']:8s} {h['name']}  ({h['root']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
