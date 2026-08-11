# mcp-triage — how to use

## Install

```
/plugin marketplace add verbalogicproject-creator/verbalogix
/plugin install mcp-triage@verbalogix
```

## Run it before a focused session

```
/mcp-triage:triage <what you're about to work on>
```

(Plugin commands are namespaced `plugin:command`. With no argument, it infers the task from your
current session/project and tells you the assumption it's judging against.)

Examples:
- `/mcp-triage:triage research the latest Next.js caching docs`
- `/mcp-triage:triage debug the Kotlin companion app`
- `/mcp-triage:triage write a KG-RAG ingestion pipeline`

## What you get

- **Turn on** — extensions that fit this task but are currently switched off, one reason each,
  with the exact command. This is the main event: an installed-but-disabled skill or plugin is
  invisible to your session until you switch it back on.
- **Already available** — relevant things you already have on. No action.
- **In your other install** — only if you run more than one Claude Code (e.g. Termux plus a PRoot
  distro). Named, and flagged as *not reachable from this session*, so you don't paste a command
  that can't work from where you are.
- **Idle for this task** — switched on but unrelated, one reason each (a server that failed to
  connect is always a disable candidate).
- **Restore block** — `claude mcp add …` rebuilt from your config (command, args, env), for any
  server you're advised to *remove*. **Copy this first** — removing a server loses its config, and
  this is how you get it back.
- **Left untouched** — your claude.ai connectors and policy-managed extensions are named but never
  removed.

Every suggestion cites the file it came from. If you want to check one, open that path — a
recommendation you can't verify isn't worth much.

Run the commands you agree with, then **restart Claude Code** — config is read at startup, so
nothing changes mid-session.

## Seeing everything, without a task

```bash
python3 scripts/catalog.py          # what's installed + what's switched on right now
python3 scripts/catalog.py --json   # structured
```

If you run a second Claude Code install, `catalog.py` says so and tells you how to include it
(`--home <path>`). It never merges the two: each install has its own plugins and its own on/off
state, and blending them would let one stand in for the other.

## Restoring later

Paste the restore command(s) from the Restore block (or re-run `/mcp-triage` and copy them again — it
reads your live config each time, so anything still configured is shown). Then restart.

You can also see every configurable server + its restore command directly, without a task:

```bash
python3 scripts/mcp_inventory.py            # human table
python3 scripts/mcp_inventory.py --json      # structured
```

## FAQ

**Does this actually save many tokens?** Trimming, no — and we won't pretend it does. Claude Code
defers MCP tool schemas, so idle servers are nearly free token-wise. The win from trimming is a
faster, quieter startup and less tool-selection noise. The bigger win is the other direction:
finding the extension you own but had switched off. If you want real token savings on a big repo,
that's what [context-os](https://github.com/verbalogicproject-creator/context-os) is for.

**Does it change my config?** No. It's read-only — it prints commands; you decide and run them.

**Could it recommend something I don't have?** It shouldn't. It reads your disk first and can only
suggest what it found there, and every suggestion carries the path of the file it came from. If
it names something, that file exists — go look.

**Why did a skill I never use show up above one I use daily?** Relevance to your task comes first;
past usage only nudges the order by a few places. That's deliberate — a tool you've never opened
can still be exactly right for a new task, and a favourite shouldn't crowd out a better match.

**Why not just `claude mcp disable`?** There is no such command — Claude Code's MCP CLI is add/remove
only, with no reversible per-server toggle for user-scope servers. That's exactly why mcp-triage shows
the restore commands up front.

**Will it remove my Gmail/Drive/Calendar?** No. Those are claude.ai connectors, managed in claude.ai —
mcp-triage names them but never advises removing them.
