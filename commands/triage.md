---
description: For the task you're about to do, recommend the minimal set of MCP servers to keep enabled and print the exact commands to disable the rest — and to restore them. Read-only; you run the commands.
argument-hint: "<what you're about to work on> (e.g. 'research Next.js caching' or 'debug the Kotlin app')"
allowed-tools: Bash(claude:*), Bash(python3:*), Read
---

# /mcp-triage

Cut MCP-server noise for a focused session. Given the task you're about to do, this recommends which
of your MCP servers to **keep** and which to **disable**, and prints the exact commands for both
directions — including the **restore** commands, captured *before* you remove anything, so nothing is
lost. It is **read-only**: it advises; you run the commands.

## Be honest about the payoff (say this, don't oversell)

Claude Code already **defers** MCP tool schemas — idle servers cost only a small tool-names amount at
startup (~120 tokens per 5 servers), not their full schemas. So the win here is **a faster, quieter
session** (fewer server processes + health-checks at startup, less selection noise), **not** big token
savings. Changes take effect **next session** (MCP config is read at startup). Never claim large token
savings.

## Request

$ARGUMENTS

## Protocol

0. **Resolve the task.** If `$ARGUMENTS` names a task, use it. If it's **empty**, infer the task from the
   current session + project context (what's being worked on, the repo/branch), and **state the
   assumption you're judging against** — e.g. "no task given; judging against &lt;inferred task&gt; — tell me
   if that's wrong and I'll re-judge." Never silently guess.

1. **Inventory the configurable servers** (user/local scope — the ones you can advise removing):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_inventory.py" --json
   ```
   Each entry has `{name, scope, transport, where, remove, restore}`. Also get **live status**:
   ```bash
   claude mcp list
   ```

2. **Judge relevance to the task.** For each configurable server, decide **Keep** or **Disable** for
   *this* task, using its name/command + what you know it does (its tool descriptions). A server that is
   `✘ Failed to connect` is a strong disable candidate regardless. Give **one honest reason** per server —
   don't pad. When unsure whether a server is needed, **Keep it** (a wrong disable costs a restart).

3. **Present the recommendation:**
   - **Keep** (needed for this task): name — one-line reason.
   - **Disable** (idle for this task): name — one-line reason.
   - **Disable commands** (effective next session): the `remove` line from the inventory for each Disable.
   - **Restore block** (tell the user to copy this FIRST): the `restore` line for each Disable server —
     these re-add the exact config (command, args, env) if they want it back. Removing loses the config,
     so this block is the safety net.

4. **Note what you did NOT touch:** any **claude.ai connectors** (Drive/Gmail/Calendar — managed in
   claude.ai, not via `claude mcp`) and any **plugin-provided** servers (toggle by disabling the plugin,
   not by removing) shown in `claude mcp list` but absent from the inventory. Name them; don't advise
   `claude mcp remove` for them.

5. **Footer** — the honesty note above (deferral; modest payoff; effective next session).

## Never

- Never run `claude mcp remove`/`add` yourself — this command only *recommends*. The user runs them.
- Never present a Disable list without the matching Restore block.
- Never claim large token savings — state the deferral fact.
