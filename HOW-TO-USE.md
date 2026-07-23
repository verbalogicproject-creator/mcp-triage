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

- **Keep** — the servers this task needs, one reason each.
- **Disable** — the servers idle for this task, one reason each (a server that failed to connect is
  always a disable candidate).
- **Disable commands** — `claude mcp remove <name> --scope <scope>` for each Disable server.
- **Restore block** — `claude mcp add …` for each, rebuilt from your config (command, args, env). **Copy
  this first** — removing a server loses its config, and this is how you get it back.
- **Left untouched** — your claude.ai connectors and plugin servers are named but not removed.

Run the disable commands you agree with, then **restart Claude Code** — MCP config is read at startup, so
nothing changes mid-session.

## Restoring later

Paste the restore command(s) from the Restore block (or re-run `/mcp-triage` and copy them again — it
reads your live config each time, so anything still configured is shown). Then restart.

You can also see every configurable server + its restore command directly, without a task:

```bash
python3 scripts/mcp_inventory.py            # human table
python3 scripts/mcp_inventory.py --json      # structured
```

## FAQ

**Does this actually save many tokens?** No — and we won't pretend it does. Claude Code defers MCP tool
schemas, so idle servers are nearly free token-wise. The win is a faster, quieter startup and less
tool-selection noise. If you want real token savings on a big repo, that's what
[context-os](https://github.com/verbalogicproject-creator/context-os) is for.

**Does it change my config?** No. It's read-only — it prints commands; you decide and run them.

**Why not just `claude mcp disable`?** There is no such command — Claude Code's MCP CLI is add/remove
only, with no reversible per-server toggle for user-scope servers. That's exactly why mcp-triage shows
the restore commands up front.

**Will it remove my Gmail/Drive/Calendar?** No. Those are claude.ai connectors, managed in claude.ai —
mcp-triage names them but never advises removing them.
