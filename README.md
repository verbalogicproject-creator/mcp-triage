# mcp-triage

**Find the skills, plugins and MCP servers your task needs — including the ones you forgot you
switched off.**

Install enough extensions and two things happen at once: your session launches a pile of servers
you don't need right now, *and* the genuinely useful thing you installed two months ago sits
switched off where you'll never think to look for it. You can't use what you can't see.

`mcp-triage` takes the task you're about to start and searches everything on disk — skills,
plugins, MCP servers, agents, commands, switched on or off — then tells you what to **turn on**
for this task and what's just **idle noise**, with the exact commands for both.

It's **read-only**: it advises, you run the commands.

## Suggestions are found, not guessed

Ask any assistant "which plugin should I use?" and it will answer from memory — confidently, and
sometimes about a plugin you've never installed. This doesn't do that. It probes your disk first
and can only recommend what it actually found, and every suggestion cites the file it came from,
so you can open it and check. If something isn't installed, it says so instead of naming it.

Matching runs as a real search over that catalog: your task text against each extension's name,
description, and body. Two touches on top:

- **Results group by plugin.** You don't enable a skill, you enable the plugin that ships it — so
  matches collapse into one entry per plugin, showing everything you'd get. A plugin's place in the
  list comes from its best match, never from how many pieces it ships.
- **What you've actually used gets a nudge.** Extensions you reach for often rank a little higher
  than ones you've never touched — bounded on purpose, so a popular tool can never crowd out a
  better match. A never-used extension can still be the right answer.

## Honest about the payoff

Two wins, different sizes:

- **Turning things on is the real one.** An installed-but-disabled extension is invisible to the
  session. Surfacing it is the point of this tool.
- **Turning things off is modest.** Claude Code already **defers** MCP tool schemas — an idle
  server costs only a small tool-*names* amount at startup, not its full schemas. So this is
  **not** a big token diet. Want the exact number on your machine? Run `/context` before and
  after — we'd rather you measure it than trust a figure we made up. What you do get is a faster,
  cleaner startup (fewer processes launched and health-checked; a flaky server that fails to
  connect is pure overhead) and less selection noise.

Changes take effect **next session** (Claude Code reads config at startup). We'd rather tell you
that than sell you a number that isn't real.

## Quickstart

```
/plugin marketplace add verbalogicproject-creator/verbalogix
/plugin install mcp-triage@verbalogix
```

Then, before a focused work session:

```
/mcp-triage:triage add a voice assistant to a Next.js app
```

You'll get a **Turn on** list (each with a one-line reason and the exact command), what's
**already available**, and — if you ask to trim — what's idle for this task, with the
`claude mcp add …` **restore** commands captured first so nothing is ever lost. Run what you want;
restart to pick it up.

## More than one Claude install?

Some setups carry two — say Termux and a PRoot distro — each with its own config, plugins, and
on/off state. Extensions from the *other* install are listed separately and clearly marked as not
reachable from this session, because offering you a command that can't work from where you are
would be worse than not mentioning it.

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
scripts/catalog.py        probes every installed extension + whether it's switched on right now
scripts/triage_rank.py    searches that catalog against your task
declared_core/            vendored search engine (byte-identical copy; see VENDORED.json)
```

Free, offline, `$0`, no server, no API keys, stdlib-only. Apache-2.0 — Eyal Nof. See `HOW-TO-USE.md`.
