# CLAUDE.md — mcp-triage (this plugin's own repo)

A one-command **read-only advisor**: for a given task, it finds which of your installed
extensions — skills, plugins, MCP servers, agents, commands — actually fit, **including ones
you have switched off**, and prints the exact commands to turn them on or trim the idle ones.
It never touches config.

The name is now narrower than the tool. Renaming is a breaking change for anyone referencing
`mcp-triage@verbalogix`, so it is deliberately deferred, not forgotten.

## Layout

- `.claude-plugin/plugin.json` — manifest (metadata only; dirs auto-discovered).
- `commands/triage.md` — the `/mcp-triage` command (frontmatter `description`/`argument-hint`/
  `allowed-tools`). The body holds the advisor protocol + the honesty rules.
- `scripts/mcp_inventory.py` — stdlib-only: reads `~/.claude.json` (user + per-project
  `mcpServers`) and emits, per server, the `claude mcp remove` and reconstructed
  `claude mcp add` (restore) commands.
- `scripts/catalog.py` — probes every extension on disk (plugins, skills, commands, agents,
  MCP servers) and resolves whether each is switched **on right now** through the real settings
  cascade. Every record carries the `path` it came from.
- `scripts/triage_rank.py` — declares the catalog as a searchable corpus and ranks it against a
  task with the vendored engine (BM25 + a usage prior), then groups hits by the
  plugin you would switch on.
- `scripts/dense.py` — OPTIONAL semantic booster. Embeds the catalog once via a local
  `/v1/embeddings` server, caches the vectors, and hands `hybrid_query` a dense index.
  Off unless `MCP_TRIAGE_EMBED_URL` is set; returns `None` on every failure path.
- `declared_core/` — **vendored, byte-identical** copy of the engine (`VENDORED.json` records
  the tree hash). Do not hand-edit; re-sync with `tools/revendor.py` from the portfolio root.
- `tests/` — pytest; fixtures only, never the developer's real config.

## Invariants — do not regress

1. **Read-only.** The command and the scripts must NEVER run `claude mcp remove`/`add` or edit
   `~/.claude.json`/settings. mcp-triage recommends; the user acts. (`mcp_inventory.py --save`,
   if ever added, may write a helper `mcp-restore.sh` only — never active config.)
2. **Restore-first.** Every MCP *removal* recommendation must come with the exact restore
   command, rebuilt from config (command, args, env, headers). Removing loses config; the
   restore block is the safety net. `test_inventory.py` locks the reconstruction.
3. **stdlib-only, offline — in the DEFAULT path.** No third-party imports and no network on the
   path a plain run takes. The vendored engine is stdlib-only too. The one exception is
   `dense.py`, and it is an exception only because it is switched off unless
   `MCP_TRIAGE_EMBED_URL` is set: numpy is imported lazily inside a `try`, the endpoint is
   localhost, and every failure returns `None`. Adding a hard dependency, or importing numpy at
   module scope, breaks this.
   **BM25 is the floor; dense is a booster that must degrade to nothing.** `dense=None` has to
   produce byte-identical output to no dense at all — server down, numpy missing, feature
   unconfigured, or the embedder dying mid-build (half an index is worse than none: it would rank
   part of the catalog semantically and the rest not at all).
   `test_dense.py::test_no_dense_index_is_byte_identical_to_lexical_only` locks this.
4. **Honest copy.** README, HOW-TO-USE, `plugin.json`, and the command output must NOT claim
   large token savings — Claude Code already defers MCP schemas. State that; sell the real win
   (surfacing extensions you own but cannot see; faster/quieter startup). No ecosystem jargon
   (NLKE/substrate/SAG/declared) in user-facing copy.
5. **Never suggest what wasn't probed.** A recommendation must trace to a catalog record with a
   real `path`. The catalog exists so suggestions are retrieved, not recalled — recall invents
   plugins the user doesn't have. `test_rank.py` locks this.
6. **Secrets never enter the catalog.** `catalog.py` must not read MCP `env` or `headers`
   values; the catalog is built to be printed and pasted. Only `mcp_inventory.py` touches them,
   because a restore line genuinely needs them. `test_catalog.py` locks this.
7. **One catalog scan = one Claude home.** This device can carry more than one install (e.g.
   Termux and a PRoot distro), each with its own config, plugins, and on/off state. Records are
   stamped with their `root` and never merged: an extension enabled in the *other* install is
   not reachable from this session, so it must never be offered as available or enable-able.
8. **The plugin groups results; it never ranks them.** Grouping by plugin is a display concern
   (`group_hits`). Do NOT declare `group_key` as a `cluster_column`: feeding that relationship
   into the ranking makes rank fusion count a plugin's size as if it were relevance, and a large
   plugin's loosely-related siblings then bury a lean plugin's exact match. This was measured, not
   theorised — see `test_group_order_follows_best_member_not_member_count`.

## Run the checks

```bash
python3 -m pytest tests/ -q
python3 scripts/mcp_inventory.py            # sanity: lists your real servers + restore commands
python3 scripts/catalog.py                  # sanity: what's installed and what's switched on
python3 scripts/triage_rank.py "debug flaky pytest"   # sanity: ranked suggestions
python3 ../tools/revendor.py check --repo mcp-triage  # vendored engine must be in sync
```

## Attribution

Eyal Nof, sole author. Apache-2.0 (`LICENSE`, `NOTICE`). No co-author trailer on commits in this repo.
Stop before pushing.
