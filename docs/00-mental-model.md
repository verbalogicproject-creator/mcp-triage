# 00 — Mental model

`mcp-triage` matches your installed extensions to the task in front of you, in **both** directions:
what to switch **on** because it fits, and what to switch **off** because it's noise.

The two directions started life as one. The original tool only did the second: there is no
reversible "disable" for a user- or local-scope MCP server — the CLI is add/remove only — so every
server you ever configured, including the one you needed for a Kotlin debugging session three weeks
ago, sits in `~/.claude.json` forever and gets launched and health-checked at every startup.

But trimming turned out to be the *smaller* problem. The bigger one is the mirror image:

> **You cannot use what you cannot see.**

An installed-but-disabled skill, plugin, or agent is invisible to a session. You won't remember it
exists, so you do the work by hand — while a plugin you already own, already vetted, sits switched
off two directories away. The machine this doc set was written on has **191 extensions installed and
most of them off**. Nobody can hold that in their head.

So the tool's job is: describe your task in plain words, and get back the things you already own
that fit it — with the exact command to turn each one on, and the path to the file it came from.

It is **read-only**. It never enables, disables, removes, or edits anything. It prints commands.
You decide, you paste, you restart.

## Found, not recalled

Ask any assistant "which plugin should I use for this?" and it answers from memory — confidently,
and sometimes about plugins you have never installed. That failure is the reason this repo has a
`catalog.py` at all.

Nothing can be suggested here unless it was **probed off your disk first**, and every suggestion
carries the `path` it came from so you can open it and check. If something isn't installed, the tool
says so rather than naming it. [Chapter 02](02-the-catalog.md) is about that probe;
[chapter 03](03-ranking.md) is about how a task description gets matched against it.

## Be honest about the payoff before you go further

Two wins, and they are **not** the same size.

**Switching things on** is the real one. An extension you can't see has a 100% cost — you rebuild
its work by hand — and surfacing it removes that entirely.

**Switching things off** is modest, and this repo says so on purpose. It would be easy to assume
"fewer configured servers = fewer tokens burned per session." That's **not quite right**: Claude
Code already **defers** MCP tool schemas — only tool *names* load into context at session start, not
full schemas for every tool on every server. An idle server is already cheap, token-wise. So
trimming is not a big token diet. What it does buy:

- a **faster, quieter startup** (fewer processes launched and health-checked — real time, worse on
  slower machines, and a server that fails to connect is pure overhead), and
- **less tool-selection noise** (fewer candidate tools means fewer wrong-tool reaches for a focused
  task).

If you want an actual token number for your own setup, run `/context` before and after triaging —
that's the only honest source, and it's what the README tells you to do too.

## Build the running example

This doc set has two running examples, because the tool has two halves:

| Chapters | Example | Code under the hood |
|---|---|---|
| 00–01 | a hand-built MCP config file | `scripts/mcp_inventory.py` |
| 02–03 | a hand-built fake Claude *home* | `scripts/catalog.py`, `scripts/triage_rank.py` |

Both are fixtures. Neither needs your real `~/.claude.json` — that's the point, they reproduce
anywhere. Start with the first: a tiny MCP config run through `scripts/mcp_inventory.py`.

Create a fixture config with exactly one `stdio` server:

```bash
cat > /tmp/mcp-triage-demo.json <<'EOF'
{
  "mcpServers": {
    "docs-search": {
      "type": "stdio",
      "command": "python3",
      "args": ["docs_search_server.py", "--serve"],
      "env": {"DOCS_ROOT": "/home/me/docs"}
    }
  }
}
EOF
```

Now run the inventory script against it (from the repo root):

```bash
python3 scripts/mcp_inventory.py --path /tmp/mcp-triage-demo.json
```

Expected output (this is real — copied verbatim from an actual run):

```
1 configurable MCP server(s) (user/local scope):

  docs-search  [user, stdio]  python3

# disable (removes it — effective next session):
  claude mcp remove docs-search --scope user

# restore (copy these FIRST — removing loses the config):
  claude mcp add docs-search --scope user -e DOCS_ROOT=/home/me/docs -- python3 docs_search_server.py --serve
```

Read that output like this:
- `docs-search  [user, stdio]  python3` — one line per server: name, scope (`user` because it came
  from the top-level `mcpServers` key), transport, and where it runs.
- The **disable** line is the literal command you'd run to remove it — nothing runs it for you.
- The **restore** line is `_restore_cmd`'s reconstruction of the exact `claude mcp add` that put it
  there in the first place: command, args, and the one env var, rebuilt from your config, not
  stored anywhere new.

## Verify your build

Run the same two commands yourself:

```bash
cat > /tmp/mcp-triage-demo.json <<'EOF'
{"mcpServers":{"docs-search":{"type":"stdio","command":"python3","args":["docs_search_server.py","--serve"],"env":{"DOCS_ROOT":"/home/me/docs"}}}}
EOF
python3 /path/to/mcp-triage/scripts/mcp_inventory.py --path /tmp/mcp-triage-demo.json
```

You should see the exact 8-line block above, byte-for-byte (only the `DOCS_ROOT` value is yours to
change). If you see `No user/local-scope MCP servers found in ~/.claude.json.` instead, that
message is fixed text — it prints for *any* unreadable or empty config, whether the file is missing,
the path is mistyped, or the JSON has no `mcpServers` key (`load_config` swallows read/parse errors
and returns `{}` rather than raising). Double-check the path you passed after `--path` actually
exists and contains the fixture above.

## The chapters

| # | Chapter | What it covers |
|---|---|---|
| 00 | Mental model | this page |
| 01 | [Inventory and restore](01-inventory-and-restore.md) | `mcp_inventory.py` — MCP servers, and why removal is restore-first |
| 02 | [The catalog](02-the-catalog.md) | `catalog.py` — probing every extension and whether it's on *right now* |
| 03 | [Ranking](03-ranking.md) | `triage_rank.py` — matching a task against the catalog |
| 04 | [The triage workflow](04-the-triage-workflow.md) | `commands/triage.md` — the slash command that ties it together |
| 05 | [Testing and CI](05-testing-and-ci.md) | what's locked down, and how |

Next: [01 — Inventory and restore](01-inventory-and-restore.md) extends this same fixture with a
second server (an `http` one) and a project-scoped one, and walks through why the restore command
looks the way it does.
