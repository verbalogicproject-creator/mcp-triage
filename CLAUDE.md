# CLAUDE.md — mcp-triage (this plugin's own repo)

A one-command **read-only advisor** that, for a given task, recommends which MCP servers to keep vs.
disable and prints the exact remove + restore commands. It never touches config.

## Layout

- `.claude-plugin/plugin.json` — manifest (metadata only; dirs auto-discovered).
- `commands/mcp-triage.md` — the `/mcp-triage` command (frontmatter `description`/`argument-hint`/
  `allowed-tools`). The body holds the advisor protocol + the honesty rules.
- `scripts/mcp_inventory.py` — stdlib-only: reads `~/.claude.json` (user + per-project `mcpServers`) and
  emits, per server, the `claude mcp remove` and reconstructed `claude mcp add` (restore) commands.
- `tests/` — pytest; `test_inventory.py` parses fixtures.

## Invariants — do not regress

1. **Read-only.** The command and the script must NEVER run `claude mcp remove`/`add` or edit
   `~/.claude.json`/settings. mcp-triage recommends; the user acts. (`mcp_inventory.py --save`, if ever
   added, may write a helper `mcp-restore.sh` only — never active config.)
2. **Restore-first.** Every Disable recommendation must come with the exact restore command, rebuilt from
   config (command, args, env, headers). Removing loses config; the restore block is the safety net.
   `test_inventory.py` locks the reconstruction.
3. **stdlib-only, offline.** No third-party imports. Runs on the system Python 3.
4. **Honest copy.** README, HOW-TO-USE, `plugin.json`, and the command output must NOT claim large token
   savings — Claude Code already defers MCP schemas. State that; sell the real win (faster/quieter
   startup, less selection noise). No ecosystem jargon (NLKE/substrate/SAG/declared) in user-facing copy.

## Run the checks

```bash
python3 -m pytest tests/ -q
python3 scripts/mcp_inventory.py            # sanity: lists your real servers + restore commands
```

## Attribution

Eyal Nof, sole author. Apache-2.0 (`LICENSE`, `NOTICE`). No co-author trailer on commits in this repo.
Stop before pushing.
