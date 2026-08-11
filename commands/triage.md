---
description: For the task you're about to do, find the skills, plugins, and MCP servers that fit — including ones you have switched off — and recommend what to turn on and what to leave idle. Read-only; you run the commands.
argument-hint: "<what you're about to work on> (e.g. 'add voice to a Next.js app' or 'debug flaky pytest')"
allowed-tools: Bash(claude:*), Bash(python3:*), Read
---

# /mcp-triage

Match your installed extensions to the task in front of you — **both directions**. Some things
you own are switched off and would help; others are switched on and are just noise for this
task. This finds both and prints the exact commands. It is **read-only**: it advises; you run
the commands.

## Be honest about the payoff (say this, don't oversell)

Two different wins, and they are not the same size:

- **Turning things on** is the real one. Anything installed but switched off is invisible to
  the session — you cannot use what you cannot see. Surfacing it is the point.
- **Turning things off** is modest. Claude Code already **defers** MCP tool schemas, so idle
  servers cost only a small tool-names amount at startup, not their full schemas. The win is a
  faster, quieter session (fewer server processes + health-checks, less selection noise), **not**
  big token savings.

Changes take effect **next session** (config is read at startup). Never claim large token
savings, and do not quote a specific per-server token figure — if the user wants a number, tell
them to run `/context` before and after; that is the only honest source.

## Request

$ARGUMENTS

## Protocol

0. **Resolve the task.** If `$ARGUMENTS` names a task, use it. If it's **empty**, infer the task
   from the current session + project context (what's being worked on, the repo/branch), and
   **state the assumption you're judging against** — e.g. "no task given; judging against
   &lt;inferred task&gt; — tell me if that's wrong and I'll re-judge." Never silently guess.

1. **Search the catalog.** One command probes every extension on disk and ranks it against the
   task:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triage_rank.py" "<the task>" --json
   ```
   It returns four buckets:
   - `turn_on` — relevant, but switched off in **this** install. The actionable list.
   - `already` — relevant and already available. Nothing to do.
   - `elsewhere` — relevant, but installed under a **different Claude home** on this machine
     (this device can carry more than one). Not reachable from this session.
   - `idle` — switched on here, but unrelated to this task. Disable candidates.

   Each entry carries `path`, `enable_cmd`, `state_reason`, and `usage_count`. For MCP servers,
   also get live status and the restore-safe removal lines:
   ```bash
   claude mcp list
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_inventory.py" --json
   ```

2. **Never suggest anything the search didn't return.** Every recommendation must come from a
   bucket above and must cite its `path`. If you expect something to exist but it isn't in the
   output, say it isn't installed — do not name it as if it were. Probing the catalog instead of
   recalling it is the whole point; recalling names is how you end up recommending a plugin the
   user doesn't have.

3. **Present the recommendation**, shortest useful form:
   - **Turn on** (relevant, currently off): name — one-line reason it fits — the `enable_cmd`
     verbatim. When several hits share one plugin, say so once: you enable the **plugin**, and
     its siblings come with it.
   - **Already available**: name — one line. No action.
   - **In your other install** (only if `elsewhere` is non-empty): name it, and say plainly it
     is not available in this session. Do **not** hand over an enable command for it — it cannot
     work from here.
   - **Idle for this task** (only if the user asked to trim): name — one-line reason — the
     `disable_cmd`. For MCP servers being *removed* rather than disabled, restore block first
     (step 4).
   - **How to check it worked**: one cheap probe per suggestion — re-running the command, or
     `claude mcp list` for a server.

4. **Restore-first, for MCP removal only.** `claude mcp remove` deletes the server's config and
   wipes its OAuth tokens. If you recommend removing one, print its `restore` line from
   `mcp_inventory.py` **first** and tell the user to copy it before running anything. Prefer
   `/mcp disable <server>` where it applies — reversible, no restore block needed.

5. **Note what you did NOT touch:** claude.ai connectors (Drive/Gmail/Calendar — managed in
   claude.ai, not via `claude mcp`) and anything enabled by managed policy. Name them; don't
   advise removing them.

6. **Footer** — the honesty note above (deferral; modest disable payoff; effective next session).

## Judgement rules

- **Ranked ≠ right.** The search returns candidates; you still decide. Drop a hit that clearly
  doesn't fit rather than padding the list to look thorough.
- **When unsure, lean toward enabling / keeping.** A wrong disable costs a restart; a wrong
  enable costs almost nothing.
- A server showing `✘ Failed to connect` is a strong disable candidate regardless of rank.
- **Usage is a prior, not proof.** `usage_count` shows you have reached for something before,
  which is real evidence — but a never-used extension can still be the right answer for a new
  task. Don't reject a good fit for having a zero.

## Never

- Never run `claude mcp remove`/`add`, edit settings, or toggle a plugin yourself — this command
  only *recommends*. The user runs them.
- Never name an extension that wasn't in the search output.
- Never offer an enable command for something in the `elsewhere` bucket.
- Never present an MCP **removal** without its restore line.
- Never claim large token savings — state the deferral fact.
