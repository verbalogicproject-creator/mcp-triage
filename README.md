# mcp-triage

**Keep only the MCP servers your task actually needs — for a faster, quieter session.**

Accumulate enough MCP servers and every Claude Code session launches and health-checks a pile of them
you don't need for what you're doing right now. `mcp-triage` looks at the task you're about to start,
recommends which servers to **keep** and which to **disable**, and prints the exact commands for both —
including the **restore** commands, captured *before* you remove anything, so nothing is ever lost.

It's **read-only**: it advises, you run the commands.

## Honest about the payoff

Claude Code already **defers** MCP tool schemas — an idle server costs only a small tool-*names* amount
at startup (~120 tokens per 5 servers), not its full schemas. So this is **not** a big token diet.
The real wins are:

- **A faster, cleaner startup** — fewer server processes launched and health-checked (real on slower
  machines; a flaky server that fails to connect is pure overhead).
- **Less selection noise** — fewer tools means fewer wrong-tool reaches and a more focused session.

Changes take effect **next session** (Claude Code reads MCP config at startup). We'd rather tell you
that than sell you a number that isn't real.

## Quickstart

```
/plugin marketplace add verbalogicproject-creator/verbalogix
/plugin install mcp-triage@verbalogix
```

Then, before a focused work session:

```
/mcp-triage:triage debug the Kotlin companion app
```

You'll get a **Keep** list and a **Disable** list (each with a one-line reason), the exact
`claude mcp remove …` commands, and — crucially — the `claude mcp add …` **restore** commands so you can
put everything back with one paste. Run what you want; restart to pick it up.

## Why restore-first matters

Claude Code has **no reversible "disable"** for user-scope MCP servers — the CLI is add/remove only. So
"disabling" is really *remove now, re-add later*. mcp-triage reconstructs each server's full re-add
command (command, args, env vars, headers) from your config and shows it **before** you remove, so a
`claude mcp remove` can always be undone.

## What it won't touch

It only advises on servers you can actually `claude mcp remove` (user/local scope). It **names but
leaves alone** your claude.ai connectors (Drive/Gmail/Calendar — managed in claude.ai) and any
plugin-provided servers (toggle those by disabling the plugin, not by removing).

## Layout

```
commands/triage.md        the /mcp-triage:triage advisor (read-only)
scripts/mcp_inventory.py  reads ~/.claude.json -> per-server remove + restore commands (stdlib)
```

Free, offline, `$0`, no server, no API keys, stdlib-only. Apache-2.0 — Eyal Nof. See `HOW-TO-USE.md`.
