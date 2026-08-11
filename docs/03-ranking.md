# 03 — Ranking

[Chapter 02](02-the-catalog.md) produced a list of every extension and whether it's switched on.
This chapter is about turning "here is everything you own" into "here is what fits *this* task".

## Why this is a search problem

The catalog on a real machine runs to a few hundred records across two installs. You could paste
that into a session and ask the assistant to pick — but that's a *recall* problem, and recall
degrades exactly where it's needed most: the long tail of things you installed once and forgot.
That tail is the whole reason the tool exists.

So matching runs as retrieval over an index instead. The catalog is declared as a searchable corpus
and queried with the vendored engine in `declared_core/` — BM25 over each extension's name,
description, and body, scored across the engine's declared dimensions and fused. Two consequences
worth stating plainly:

- Results can only ever be **things the probe found**. Retrieval cannot return a row that isn't in
  the table, which is the structural version of "don't invent plugins".
- Ranking is **deterministic**. Same catalog, same query, same order — no wall clock, no randomness
  in the path. Re-running for a second opinion gets you the same answer, which is the honest
  behaviour even if it's the less magical one.

## The plugin groups results; it does not rank them

**You don't switch on a skill, you switch on the plugin that ships it**, and its siblings arrive
whether you asked for them or not. So hits collapse into one group per plugin, showing the real unit
of decision:

```python
groups = group_hits(hits)   # ordered by each group's BEST member, never by size
```

Rows with no plugin (a lone MCP server, a loose agent) get their own synthetic key rather than
sharing an empty string, so they display alone instead of piling into one bucket.

### Why grouping is display-only — a bug worth keeping

The first version expressed that same relationship in the *corpus*, as a structural hop over a
plugin cluster column:

```python
cluster_columns=("cluster_key",)     # removed
```

It looked principled and it was measurably wrong. Rank fusion combines lists by **position**, so an
item appearing in both the lexical list and the structural list outscores one appearing in the
lexical list alone. A plugin shipping five components therefore contributed five double-counted
entries; a lean plugin with one command contributed one. Plugin *size* was acting as relevance.

Measured on the real catalog, with a query taken almost verbatim from `vouch`'s own description:

```
BM25 alone:   1. /vouch:vouch          2. vouch@verbalogix        <- correct
After fusion: 1. repo-factory-documenter   2. atlas-kb-ingester
              3. memory-recall             4. map-updater
              5. vouch@verbalogix          8. /vouch:vouch        <- buried
```

Every hit that displaced it arrived via `structural:cluster_key`. Dropping the cluster column moved
the right answer from 12th to 2nd, and made the top-ranked *plugin* correct on all four tasks in the
regression battery.

The lesson generalises past this repo: **a relationship that is real in the domain is not
automatically a good ranking signal.** "These ship together" is true and useful — it just belongs in
how results are presented, not in how they are scored.
`test_group_order_follows_best_member_not_member_count` locks it, and invariant 8 in `CLAUDE.md`
warns the next person off re-adding the column.

## Usage: a prior, not evidence

"You have used this 42 times" is real information, and it belongs in the ranking. It is declared as
a `proven` dimension, scored from the counters chapter 02 collected:

```python
PROVEN = DimensionDef(
    name="proven",
    group="evidence",
    description="How much real use this extension has on record here.",
)
```

The score is log-scaled and saturating, so the step from *never used* to *used once* counts for far
more than the step from 900 uses to 1000:

| uses | score |
|---|---|
| 0 | 0.0000 |
| 1 | 0.3253 |
| 10 | 0.5103 |
| 100 | 0.7511 |
| 1000 | 1.0000 |

It is count-based and never clock-based. Recency would read the wall clock, and the engine forbids
that in the retrieval path because it would make results irreproducible.

### The dimension alone was not enough — and that was a real bug

Declaring `proven` made its value *visible*, but measurement showed it barely reached the output: it
is one of thirteen dimensions feeding the rules signal, which is in turn one of four **rank**-fused
lists. Two otherwise-identical extensions, one used 500 times and one never, came back in the wrong
order. The claim "your proven tools rank above ones you've never touched" was simply not true of
what the tool printed.

The fix follows from what usage actually *is*. It isn't evidence of relevance — it's a prior. So it
gets a prior's job: reorder what already matched, by a bounded amount.

```python
PROVEN_LIFT_POSITIONS = 3.0
```

A heavily-used extension climbs at most three places, and the lift is applied to a candidate pool
wider than the visible window, so a proven tool sitting just below the cut can actually surface.
Crucially it **cannot introduce anything retrieval didn't return** — that property is what keeps a
suggestion honest, and `tests/test_rank.py` locks it:

| Test | Locks |
|---|---|
| `test_lift_never_introduces_something_retrieval_did_not_return` | A never-matching row stays absent no matter how heavily used |
| `test_usage_cannot_hijack_an_unrelated_query` | A popular irrelevance can't outrank a real match |
| `test_lift_is_bounded_to_its_declared_positions` | The climb never exceeds the declared bound |

Relevance first, habit second. A tool you've never opened can still be the right answer for a new
task.

## Optional: the semantic booster

Lexical search cannot bridge a vocabulary gap. These two sentences mean the same thing and share
no content words:

> "stop the assistant inventing things about my code"
> "Fact-check the checkable claims in Claude's last answer against your real files"

BM25 ranked that answer **10th**. No amount of tuning fixes it, because the signal isn't there.

`dense.py` adds an optional second signal: embed every catalog entry once, embed the query at
run time, and let `hybrid_query` fuse cosine similarity with BM25. Measured on the real catalog
with `embeddinggemma-300m` served locally:

| Query | BM25 | Hybrid |
|---|---|---|
| "stop the assistant inventing things about my code" | 10 | **1** |
| "something to double-check my work before I ship it" | 23 | **4** |
| "help me pick which of my tools to use" | 4 | **1** |
| "I keep forgetting why we chose this approach" | 2 | **1** |

Five of six test queries land at rank 1, and — the part that matters — **no query got worse**.
That is the BM25 floor doing its job: dense adds recall where lexical had none, and lexical
holds the line where dense would have wandered.

### Why an embedder and not a reranker

A cross-encoder reranker was tried first and rejected on evidence. It scores every
`(query, document)` pair at run time, so nothing precomputes and cost scales with the candidate
pool. Worse, it produced **no net ranking gain** here: extension descriptions are short, terse,
and structurally similar, so on a genuine paraphrase every candidate collapsed into a narrow
negative band (−10.8 to −11.0) where ordering is noise. It sharpened the queries that were
already easy and added noise to the hard ones.

An embedder inverts the cost. Documents are embedded **once**; the catalog only changes when you
install or upgrade something. On the same hardware:

| | build | per query |
|---|---|---|
| cross-encoder reranker | n/a (nothing cacheable) | 1.10s |
| embeddings, cached | 16.7s one-off | **0.10s** |

### It has to degrade to nothing

The booster is switched off unless `MCP_TRIAGE_EMBED_URL` is set, and every failure path returns
`None` — no server, no numpy, a timeout, a malformed response. `hybrid_query(dense=None)`
produces results byte-identical to pure lexical search, so the default path carries no risk from
a feature it never uses.

One deliberate strictness: if the embedder dies **mid-build**, the whole index is discarded
rather than kept partial. Half an index would rank some of the catalog semantically and the rest
lexically — silently, and differently on every run.

The cache is keyed on the catalog's text plus the embedder's identity, so installing or upgrading
an extension rebuilds it and switching embedding models rebuilds it (vectors from one model are
meaningless to another). Enabled state is deliberately **not** part of the key: toggling plugins
on and off is the most common thing this tool leads you to do, and it stays free.

## The four buckets

`partition()` splits results by what you can actually *do* about them:

| Bucket | Meaning |
|---|---|
| `turn_on` | Relevant, switched off, in this install — the actionable list |
| `already` | Relevant and already available |
| `elsewhere` | Relevant, but in a different Claude home — unreachable from here |
| `idle` | Available here but unrelated to this task — disable candidates |

`elsewhere` is separate on purpose. Folding it into `already` would claim a capability the session
doesn't have; folding it into `turn_on` would hand over an enable command that can't work from
where you are.

## Run it against the fixture

Using the fake home from [chapter 02](02-the-catalog.md):

```bash
python3 scripts/triage_rank.py "deploy a service to production" \
  --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj --limit 3
```

Real output:

```
Task: deploy a service to production
Searched 5 extension(s) · this install: /tmp/mcp-triage-demo-home

Switched off, but relevant — consider turning on:

  deploy@demo-market  (+1 more piece(s))
    set "deploy@demo-market": true in enabledPlugins (or run /plugin)
      skill    deploy:ship-it
               /tmp/mcp-triage-demo-home/.claude/plugins/cache/demo-market/deploy/1.0.0/skills/ship-it/SKILL.md
      plugin   deploy@demo-market
               /tmp/mcp-triage-demo-home/.claude/plugins/cache/demo-market/deploy/1.0.0

Already available and relevant:
  skill    notes:note-search  (used 42x)
```

Three things that output demonstrates at once:

1. Both matches sit under **one plugin heading with one enable command** — the real unit of
   decision, rather than a skill you can't switch on by itself.
2. Both hits carry a **path** you can open.
3. `note-search` appears under "already available" — matched partly on its 42 recorded uses, and
   correctly *not* offered as something to turn on, because it's already on.

The first root passed to `--home` is the one being triaged for; any further `--home` roots are
reference only and land in `elsewhere`.

## Verify your build

```bash
python3 scripts/triage_rank.py "deploy a service to production" \
  --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj --limit 3
```

Expected: the block above. Then check determinism — the same query twice must be byte-identical:

```bash
Q="deploy a service to production"
A=$(python3 scripts/triage_rank.py "$Q" --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj --json)
B=$(python3 scripts/triage_rank.py "$Q" --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj --json)
[ "$A" = "$B" ] && echo DETERMINISTIC
```

Expected: `DETERMINISTIC`. Then confirm the honesty property — a query matching nothing returns
nothing, rather than padding the list with your most-used extensions:

```bash
python3 scripts/triage_rank.py "xyzzy plugh quantum basketweaving" \
  --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj
```

Expected: `Nothing switched off looks relevant to this task.` and no "already available" section —
`note-search`'s 42 uses do not buy it a place in an unrelated result.

Next: [04 — The triage workflow](04-the-triage-workflow.md) covers `commands/triage.md`, the slash
command that runs all of this inside a live session.
