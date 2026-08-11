# 04 — The triage workflow

Chapters 00–03 ran the scripts directly from a shell. That's the plumbing. The thing you actually
invoke day-to-day is a Claude Code **slash command**, `/mcp-triage:triage`, defined in
`commands/triage.md`. This chapter is about that command: what it's allowed to do, what protocol it
follows, and how to reproduce what it sees.

## Why this is a markdown file, not a script

`commands/triage.md` is not executable Python — it's a prompt with frontmatter, run by the assistant
inside a live Claude Code session:

```yaml
description: For the task you're about to do, find the skills, plugins, and MCP servers that fit —
  including ones you have switched off — and recommend what to turn on and what to leave idle.
  Read-only; you run the commands.
argument-hint: "<what you're about to work on> (e.g. 'add voice to a Next.js app' or 'debug flaky
  pytest')"
allowed-tools: Bash(claude:*), Bash(python3:*), Read
```

`allowed-tools` is where the read-only invariant becomes a real permission boundary, not just a
promise in prose: the command is only ever allowed to run `claude …` and `python3 …` (plus plain
file `Read`). There is no write tool in that list, and — separately, by policy in the body text —
the command is never allowed to run `claude mcp remove`/`add`, edit settings, or toggle a plugin,
even though `Bash(claude:*)` would technically permit the first. Two independent layers say the same
thing: this command recommends, it doesn't act.

## The protocol, step by step

1. **Resolve the task.** If you gave `$ARGUMENTS` (e.g. `/mcp-triage:triage add voice to a Next.js
   app`), use it verbatim. If you ran it bare, the command infers a task from your current
   session/project and **states the assumption out loud** — "no task given; judging against
   &lt;inferred task&gt;" — rather than silently guessing.

2. **Search the catalog.** One command does the probe and the ranking together:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triage_rank.py" "<the task>" --json
   ```
   This is [chapter 03](03-ranking.md)'s `partition()` output — `turn_on`, `already`, `elsewhere`,
   `idle` — run against your *real* config. For MCP servers specifically it also runs the chapter-01
   inventory and a live health check:
   ```bash
   claude mcp list
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_inventory.py" --json
   ```
   `claude mcp list` is the only source of live health: a server that is `✘ Failed to connect` is a
   disable candidate regardless of how well it ranked.

3. **Never suggest anything the search didn't return.** Every recommendation must come from a bucket
   and must cite its `path`. If the assistant expects something to exist but it isn't in the output,
   it says so rather than naming it. This is the policy layer of the same property
   [chapter 03](03-ranking.md) enforces structurally — retrieval can't return a row that isn't in
   the table, and the command isn't allowed to add one back from memory.

4. **Present the recommendation.** Turn on (with the `enable_cmd` verbatim, noting when several hits
   share one plugin), already available, in-your-other-install (named, with **no** enable command —
   it wouldn't work from here), idle-for-this-task if you asked to trim, and one cheap probe per
   suggestion so you can confirm it took effect.

5. **Restore-first, for MCP removal only.** `claude mcp remove` deletes the server's config and
   wipes its saved logins, so a removal recommendation always prints the chapter-01 `restore` line
   first. `/mcp disable <server>` is preferred where it applies — reversible, no restore block
   needed.

6. **Note what it didn't touch.** claude.ai connectors (Drive/Gmail/Calendar, managed in claude.ai)
   and anything enabled by managed policy get **named**, never a removal command. Chapter 01
   explained why connectors never appear in the MCP inventory in the first place: the script has no
   code path that reads them.

7. **Footer.** Restates the honesty note from [chapter 00](00-mental-model.md): schema deferral,
   modest *disable* payoff, changes take effect next session.

## Judgement is still required

The search returns candidates, not verdicts. The command body is explicit that the assistant should
drop a hit that clearly doesn't fit rather than padding the list to look thorough, and that when
unsure it should lean toward **enabling or keeping** — a wrong disable costs a restart, a wrong
enable costs almost nothing.

It also warns against over-reading the usage counter: `usage_count` is a prior, not proof. A
never-used extension can be exactly right for a new task. That's the prose counterpart to the
bounded lift in [chapter 03](03-ranking.md).

## Reproducing what the command sees, by hand

You can't invoke `/mcp-triage:triage` from a plain shell — it only runs inside a Claude Code
session. But its inputs are the commands from step 2, and you already have them working. Against
the fake home from chapters 02–03:

```bash
python3 scripts/triage_rank.py "deploy a service to production" \
  --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj --json
```

and, in a real session, `claude mcp list`. Its output has a stable shape: one line per server,
`<name>: <command-or-url> - <status>`, with claude.ai connectors prefixed `claude.ai <Name>:`,
plugin-provided servers prefixed `plugin:<plugin-name>:<server>:`, and status either `✔ Connected`
or `✘ Failed to connect — <error>`:

```
claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - ✔ Connected
plugin:some-plugin:some-server: python3 server.py - ✔ Connected
docs-search: python3 docs_search_server.py --serve - ✔ Connected
flaky-tool: node build/server.js - ✘ Failed to connect — connection closed
```

Feeding both into step 4: `docs-search` is one the inventory can act on; `flaky-tool` is a strong
disable regardless of task; `claude.ai Google Drive` and `plugin:some-plugin:some-server` get
**named** in step 6, never a remove command.

## Verify your build

Run both step-2 commands yourself (replace `${CLAUDE_PLUGIN_ROOT}` with this repo's path if you're
not running it as an installed plugin):

```bash
python3 scripts/triage_rank.py "debug a failing test suite" --json
claude mcp list
```

Confirm three things:

1. Every hit in the first output has a non-empty `path`, and that file exists.
2. Every MCP server in `mcp_inventory.py --json` also appears in `claude mcp list` — the inventory
   is a subset — and anything in `claude mcp list` that's *not* in it is a claude.ai connector or a
   `plugin:`-prefixed entry, never something silently missed.
3. Nothing changed. Run `python3 scripts/catalog.py | head -1` before and after; the
   available/switched-off counts must be identical, because none of this writes.

Next: [05 — Testing and CI](05-testing-and-ci.md) covers how all of the above is locked down so it
can't silently drift.
