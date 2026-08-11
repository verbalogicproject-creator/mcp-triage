CODEBASE-REPORT.md — mcp-triage module map
===========================================

This is the map: what each file is, what it does, what tests hold it in place, and how a
recommendation flows from your `~/.claude.json` to the two commands you actually run. Every fact
below was re-verified against the repo while writing this (commands run, files read, tests
executed) — see "How this was verified" at the end.

## What this repo is, in one paragraph

`mcp-triage` is a **read-only** Claude Code plugin. Given the task you're about to work on, it
searches every extension installed on your machine — skills, plugins, MCP servers, agents,
commands — and recommends what to **switch on** because it fits and what to leave **idle**, with
the exact command for each. Suggestions are *retrieved, never recalled*: nothing can be recommended
unless it was probed off disk first, and every hit carries the `path` of the file it came from. For
MCP removals it prints the `claude mcp add …` **restore** command, reconstructed from live config,
*before* anything is removed. It never edits `~/.claude.json`, never calls `claude mcp remove`/`add`
itself, and never writes anything. It is stdlib-only Python, offline, no API keys.

Both directions matter, but not equally. Trimming buys a quieter startup (schemas are deferred —
see "The honesty finding"). The larger win is the other direction: an installed-but-disabled
extension is **invisible** to a session, so you rebuild its work by hand. The machine this was
written on has 191 extensions installed, most of them off.

## The read-only invariant (load-bearing)

Nothing in this repo writes to Claude Code's MCP configuration. Concretely:

- `scripts/mcp_inventory.py` only calls `Path(path).read_text()` + `json.loads()` — there is no
  `write_text`, no `json.dump`, no subprocess call anywhere in the file (verified by reading the
  111-line source in full: the only I/O is the one `read_text` in `load_config`).
- `commands/triage.md` declares `allowed-tools: Bash(claude:*), Bash(python3:*), Read` — it can
  *run* `claude mcp list` (a read) and `python3 mcp_inventory.py` (a read), but the protocol text
  explicitly forbids the command from ever invoking `claude mcp remove`/`add` itself (see its
  "Never" section). The advisory step (deciding Keep/Disable) is prose-level judgment by the
  assistant, not a scripted mutation.
- `CLAUDE.md` states this as invariant #1 for anyone modifying this repo: "The command and the
  script must NEVER run `claude mcp remove`/`add` or edit `~/.claude.json`/settings."

In short: the repo can only ever *print* commands. A human has to copy-paste and run them, and has
to restart Claude Code for anything to take effect (MCP config is read at startup, not live).

## Repo layout

```
mcp-triage/
├── .claude-plugin/plugin.json      plugin manifest (name, version, author, description, keywords)
├── .claude-plugin/marketplace.json single-repo marketplace, so the repo installs as itself
├── commands/triage.md              the /mcp-triage:triage slash command (frontmatter + protocol)
├── scripts/mcp_inventory.py        stdlib-only: ~/.claude.json -> per-server remove + restore commands
├── scripts/catalog.py              probes EVERY extension + resolves whether it's switched on now
├── scripts/triage_rank.py          declares the catalog as a corpus, ranks it against a task
├── declared_core/                  VENDORED search engine, byte-identical (see VENDORED.json)
├── tests/                          pytest: 43 tests across inventory / catalog / ranking
├── .github/workflows/ci.yml        GitHub Actions: pytest + 4 smoke tests
├── docs/                           this doc set (00-mental-model.md .. 05-testing-and-ci.md)
│   └── inventory-2026-07-27.json      auto-generated AST module graph (stale; predates the catalog)
├── README.md, HOW-TO-USE.md, how-to.ngf.md, CLAUDE.md   user- and agent-facing docs
└── LICENSE, NOTICE                 Apache-2.0
```

### Why the engine is vendored rather than depended on

`declared_core` is the canonical retrieval engine, kept in its own repo. This repo ships a
**byte-identical copy** of it, recorded in `VENDORED.json` with a blake2b tree hash, synced by
`tools/revendor.py` from the portfolio root. That buys two things at once: the engine is a hard
dependency (ranking genuinely uses it) *and* this plugin still installs standalone with nothing to
fetch. It's safe only because the engine is itself dependency-free — CI asserts that by importing it
in a bare interpreter.

The engine deliberately does **not** live inside this repo's problem domain, and this repo does not
live inside the engine's. Claude Code's config shapes track someone else's release cadence; the
engine holds an invariant forbidding a hardcoded corpus. Keeping them separate is what lets both
stay true. `mcp-triage` is the seventh consumer of this pattern.

## Module map

| Path | Role | Lines | Exercised by |
|---|---|---|---|
| `.claude-plugin/plugin.json` | Plugin manifest: `name: mcp-triage`, `version: 0.2.0`, author Eyal Nof, Apache-2.0 | 20 | plugin install path |
| `.claude-plugin/marketplace.json` | Declares the repo as a one-plugin marketplace (`source: "."`), so a local checkout installs as itself without going through a remote | 15 | `claude plugin marketplace add <path>` |
| `commands/triage.md` | The `/mcp-triage:triage` command: frontmatter + a 7-step protocol (resolve task → search catalog → never suggest what wasn't returned → present the four buckets → restore-first for MCP removal → note what's untouched → honesty footer) | 108 | manual — assistant-executed protocol text, not unit-testable code |
| `scripts/mcp_inventory.py` | MCP servers only. `load_config`, `collect_servers` (user scope + `projects.*.mcpServers`), `_restore_cmd` (rebuild `claude mcp add …` for stdio vs http, incl. `-e KEY=v` and `--header`), `main` | 111 | `tests/test_inventory.py` + CI smoke |
| `scripts/catalog.py` | The probe. Enumerates plugins/skills/commands/agents/MCP servers; resolves `enabled` through the settings cascade; attaches usage counters; stamps each record with its `root`. Never reads MCP `env`/`headers` | 523 | `tests/test_catalog.py` + CI empty-home smoke |
| `scripts/triage_rank.py` | The search. Declares the catalog as a `CorpusSchema`, indexes into in-memory SQLite, ranks via `hybrid_query`, applies the bounded usage lift, splits into four buckets | 287 | `tests/test_rank.py` + CI path smoke |
| `declared_core/` | Vendored retrieval engine (15 modules). Not edited here — re-sync via `tools/revendor.py`; `VENDORED.json` holds the tree hash | 15 files | CI bare-import check + the drift guard |
| `tests/test_inventory.py` | 4 cases: stdio/http restore reconstruction, local scope, empty config | 41 | itself, run in CI |
| `tests/test_catalog.py` | 19 cases: frontmatter parsing, cascade precedence, stale-installPath fallback, disabled-plugin state, usage key matching, secrets exclusion, multi-root separation | 233 | itself, run in CI |
| `tests/test_rank.py` | 20 cases: every hit traces to a real row, determinism, the proven curve, lift bounds, the sibling hop, bucket partitioning | 240 | itself, run in CI |
| `.github/workflows/ci.yml` | On push to `main` and PRs: Python 3.11, pytest, then 4 smoke tests (inventory restore line, bare engine import, empty-home `[]`, every-hit-has-a-path) | 42 | GitHub Actions |
| `README.md` / `HOW-TO-USE.md` / `how-to.ngf.md` / `CLAUDE.md` | Pitch + honesty section / walkthrough + FAQ / short intuitive guide / seven agent-facing invariants | 96 / 93 / 146 / 66 | — |
| `docs/inventory-2026-07-27.json` | Auto-generated AST scan from 2026-07-27 — **stale**: it predates `catalog.py`, `triage_rank.py`, and the vendored engine. Regenerate or delete | — | — |

(Line counts are `wc -l` on 2026-08-11. Re-run `wc -l scripts/*.py tests/*.py commands/triage.md` if
this drifts.)

## Data flow

Two paths converge on the command. The MCP path (left) is chapter 01's; the catalog path (right) is
chapters 02–03's.

```
   ~/.claude.json                    a Claude HOME: settings cascade, plugin cache,
   (or --path FILE)                  installed_plugins.json, skills/commands/agents
         │                                          │
         │ read-only json.loads                     │ read-only: probe + parse frontmatter
         ▼                                          ▼
  scripts/mcp_inventory.py                  scripts/catalog.py
  collect_servers()                         build_catalog() per root
  user + projects.*.mcpServers              resolves enabled/state_reason/usage/path
         │                                          │  (never reads env or headers)
         │                                          ▼
         │                                  scripts/triage_rank.py
         │                                  CorpusSchema -> SQLite+FTS -> hybrid_query
         │                                  + bounded usage lift  (declared_core/)
         │                                          │
         │                                          ▼
         │                                  turn_on / already / elsewhere / idle
         │                                          │
         └──────────────┬───────────────────────────┘
                        ▼
          commands/triage.md  (/mcp-triage:triage)
          + `claude mcp list` (live health, separately)
                        │
                        ▼
          "turn these on" (+ enable_cmd + path)
          "these are idle" (+ restore block first, for MCP removal)
                        │
                        ▼
   a human reads it, runs the commands they agree with,
   restarts Claude Code (config loads at startup, not live)
```

Two properties are visible in that diagram rather than merely promised. **Nothing flows backwards**
— no arrow returns to config, which is the read-only invariant drawn out. And every suggestion at
the bottom descends from a probe at the top, so a recommendation always has a file behind it.

Note what never appears anywhere in this flow: claude.ai connectors (Drive/Gmail/Calendar) and
plugin-provided servers. `collect_servers` only ever reads the `mcpServers` and `projects` keys —
it has no code path that reads connector or plugin config, so those are structurally out of reach,
not just "advised against." `commands/triage.md` step 4 tells the assistant to name them (from
`claude mcp list`'s output) without ever emitting a remove command for them.

## The honesty finding — hygiene, not a token diet

This is the one claim in this repo worth stating plainly because it cuts against the obvious
assumption: **Claude Code defers MCP tool schemas.** Only tool *names* are loaded into context at
session start; full tool schemas for a server are not all loaded up front. That means an idle,
unused MCP server sitting in your config is *not* costing you its full schema weight every
session — so removing it is **not** a big token save.

This repo already gets that right, consistently, in four places:
- `README.md` — "Honest about the payoff": states the deferral, explicitly declines to quote an
  invented per-server token figure, and tells the reader to run `/context` before/after if they
  want a real number for their own machine.
- `HOW-TO-USE.md` FAQ — "Does this actually save many tokens? No — and we won't pretend it does."
- `commands/triage.md` — "Be honest about the payoff": instructs the assistant running the command
  to never claim large token savings and never quote a specific per-server figure.
- `CLAUDE.md` invariant #4 ("Honest copy") — names `plugin.json` explicitly as a place this must
  hold too.

Given that, the tool's real, verifiable value is:
1. **Fewer server processes launched and health-checked at startup** — real wall-clock time,
   worse on slower/battery-constrained machines, and a server that's `✘ Failed to connect` is pure
   dead weight regardless of tokens.
2. **Less tool-selection noise** — fewer candidate tools in front of the model for a focused task
   means fewer wrong-tool reaches.

Neither of those is a token-count claim, and the repo doesn't make one. This is a **hygiene** tool,
not a token-savings tool, and its own copy says so everywhere it matters.

**The inconsistency flagged in the 2026-07-27 pass is now fixed.** `.claude-plugin/plugin.json`'s
`keywords` array used to list `"token-savings"` alongside `"hygiene"` — reintroducing, as a tag, the
exact framing invariant #4 forbids, in the one file invariant #4 names explicitly. The keywords are
now `mcp, skills, plugins, discovery, context, hygiene, claude-code`: `"token-savings"` is gone and
`"skills"`, `"plugins"`, and `"discovery"` describe what the tool actually became.

## Tests & CI

```
$ python3 -m pytest tests/ -q
...........................................                              [100%]
43 passed in 0.61s
```

The suite maps onto the invariants in `CLAUDE.md`:
- **Restore-first** (#2): `test_stdio_restore_reconstructs_command_args_env` and `test_http_restore`
  lock that the restore command rebuilds command/args/env or transport/url/headers *exactly*, since
  removing a server loses its config and the restore block is the only safety net.
- **Never suggest what wasn't probed** (#5): `test_every_hit_corresponds_to_a_real_catalog_row` and
  `test_lift_never_introduces_something_retrieval_did_not_return` lock that suggestions come from
  the probe, and that a usage prior can reorder matches but never manufacture one.
- **Secrets never enter the catalog** (#6): `test_mcp_records_never_carry_env_or_header_values`
  plants a fake credential in a fixture and asserts it appears nowhere in the output.
- **One scan = one Claude home** (#7): `test_multi_root_keeps_same_named_extensions_distinct` and
  `test_other_install_never_appears_as_actionable` lock that two installs stay two installs and that
  the unreachable one is never offered as actionable.
- **State means what it says**: `test_local_scope_overrides_user_scope_for_plugin_state` locks the
  cascade, and `test_unresolvable_plugin_returns_none_rather_than_a_guess` locks that a missing
  directory is reported instead of invented.

Two of these exist because the behaviour was wrong first — the usage lift and the primary-root
default were both written *after* a test caught the shipped code getting it backwards. See
[`docs/05-testing-and-ci.md`](docs/05-testing-and-ci.md).

CI adds four smoke layers on top of the unit tests, each catching something the unit tests can't:
the real CLI entry point printing the exact restore line; the vendored engine importing in a bare
interpreter (the stdlib-only invariant, executable); an empty home producing exactly `[]` rather
than a best-effort guess; and every ranked hit carrying a path. Tested locally on Python 3.14.4 as
well as CI's pinned 3.11 — everything here is stdlib-only, so it isn't version-sensitive.

## Distribution

`plugin.json`: `name: mcp-triage`, `version: 0.2.0`, license Apache-2.0, author Eyal Nof
(`verbalogic.project@gmail.com`), homepage `github.com/verbalogicproject-creator/mcp-triage` (the
repo's `origin` remote). Two install routes:

- **Published:** `/plugin marketplace add verbalogicproject-creator/verbalogix` then
  `/plugin install mcp-triage@verbalogix`.
- **From a local checkout:** `.claude-plugin/marketplace.json` declares the repo as its own
  one-plugin marketplace, so `claude plugin marketplace add <path>` + `claude plugin install
  mcp-triage@mcp-triage` installs the working tree without a round trip through the remote. Note
  the install is a *snapshot copy* into the plugin cache, not a symlink — later edits need a
  reinstall to go live.

**The name is now narrower than the tool.** It covers skills, plugins, agents, and commands, not
just MCP servers. Renaming would break anyone referencing `mcp-triage@verbalogix`, so it is
deliberately deferred — recorded here and in `CLAUDE.md` rather than quietly forgotten.

## How this was verified

Every command shown in this file and in `docs/00-mental-model.md` through
`docs/05-testing-and-ci.md` was actually run in this repo on 2026-08-11: the full pytest suite,
`mcp_inventory.py` (default and `--json`), `catalog.py` and `triage_rank.py` (against both the live
config and the hand-built fake-home fixture), all four CI smoke steps, the vendor drift guard, and a
read of every source file in full. The determinism and nonsense-query checks in
`docs/03-ranking.md` were run and their stated outputs are the real ones.

Fixture examples in `docs/` use invented names (`docs-search`, `weather-api`, `notes@demo-market`)
and a fake home under `/tmp`, never this machine's real config, so they stay reproducible for any
reader. Where a doc shows output that *is* machine-specific — the unresolved-plugin list, the
"another Claude install exists" notices — it says so rather than implying byte-identical
reproduction.

One known-stale artifact is called out rather than silently left: `docs/inventory-2026-07-27.json`
is an AST snapshot from the previous pass and predates `catalog.py`, `triage_rank.py`, and the
vendored engine.
