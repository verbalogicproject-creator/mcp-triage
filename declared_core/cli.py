"""``declared-core`` command-line interface.

A thin shell over the library, wired to the built-in demo corpus so you can
explore every signal without writing code first. Real corpora are declared in
Python (a ``CorpusSchema``) — see the docs — but ``query``/``classify``/``dims``
here exercise the exact same engine against the demo data.

Subcommands:
    query <text>     hybrid retrieval over the demo corpus
    classify <text>  show the intent + fusion weights a query maps to
    dims             list the default dimension schema
    demo             build the demo corpus to a SQLite file

Every subcommand accepts ``--json`` for scriptable output.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .demo import DEMO_SCHEMA, build_demo, hash_embedder
from .dimensions import DEFAULT as DEFAULT_DIMS
from .retrieval import NumpyVectorIndex, classify_intent, hybrid_query


def _cmd_query(args: argparse.Namespace) -> int:
    conn, schema = build_demo(args.db or ":memory:")
    dense = None
    if args.dense:
        # Build a dense index over the demo rows' text, using the toy embedder.
        from .retrieval.bm25 import bm25_search
        from .retrieval.fusion import _annotate

        # Embed the full corpus: pull every row via a match-all-ish sweep.
        rows: list[dict] = []
        for src in schema.sources:
            for term in ("a", "e", "i", "o", "the"):  # cheap coverage sweep
                rows.extend(bm25_search(conn, schema, term, limit=100))
        seen = set()
        uniq = []
        for r in rows:
            k = (r.get("table"), r.get("id"))
            if k in seen:
                continue
            seen.add(k)
            _annotate(r, schema)
            uniq.append(r)
        dense = NumpyVectorIndex.from_items(hash_embedder(), uniq)

    result = hybrid_query(
        args.query, schema, conn, limit=args.limit, dense=dense,
        use_intent=not args.no_intent,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    print(f"query   : {result.query}")
    print(f"intent  : {result.intent}   mode: {result.mode}   "
          f"candidates: {result.total_candidates}")
    print(f"hits ({len(result.hits)}):")
    for h in result.hits:
        text = h.get("_text") or h.get("title") or h.get("claim") or "(no text)"
        score = h.get("rrf_score") or h.get("weighted_score") or 0.0
        srcs = ",".join(sorted(set(h.get("rrf_sources", [])))) or "-"
        print(f"  [{h.get('table')}:{h.get('id')}] {score:.4f}  ({srcs})  {text[:72]}")
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    r = classify_intent(args.query)
    if args.json:
        print(json.dumps(
            {"intent": r.intent, "confidence": r.confidence,
             "weights": {"bm25": r.weights[0], "structural": r.weights[1],
                         "rules": r.weights[2], "dense": r.weights[3]}},
            indent=2))
        return 0
    print(str(r))
    return 0


def _cmd_dims(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(
            [{"name": d.name, "group": d.group, "higher_is": d.higher_is,
              "weight": d.weight, "description": d.description} for d in DEFAULT_DIMS],
            indent=2))
        return 0
    print(f"default dimension schema ({len(DEFAULT_DIMS)} dims):")
    for d in DEFAULT_DIMS:
        print(f"  {d.name:22s} [{d.group:12s}] {d.description}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    path = args.db or "demo.db"
    conn, schema = build_demo(path)
    n_notes = conn.execute("SELECT count(*) FROM notes").fetchone()[0]
    n_facts = conn.execute("SELECT count(*) FROM facts").fetchone()[0]
    conn.close()
    print(f"built demo corpus at {path}: {n_notes} notes, {n_facts} facts")
    print("try:  declared-core query \"how does ranking work\" --db " + path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="declared-core",
        description="Declared, AI-optional hybrid retrieval.",
    )
    p.add_argument("--version", action="version", version=f"declared-core {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="hybrid retrieval over the demo corpus")
    q.add_argument("query")
    q.add_argument("--db", help="SQLite file to query (default: fresh in-memory demo)")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--dense", action="store_true", help="enable the (toy) dense signal")
    q.add_argument("--no-intent", action="store_true", help="disable intent routing (uniform RRF)")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=_cmd_query)

    c = sub.add_parser("classify", help="show the intent + weights for a query")
    c.add_argument("query")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_classify)

    d = sub.add_parser("dims", help="list the default dimension schema")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_dims)

    m = sub.add_parser("demo", help="build the demo corpus to a SQLite file")
    m.add_argument("--db", help="output path (default: demo.db)")
    m.set_defaults(func=_cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
