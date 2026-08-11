# 01 — Inventory and restore

Chapter 00 ran `scripts/mcp_inventory.py` against a one-server fixture. This chapter extends the
*same* fixture — same file, same running example — with an `http`-transport server and a
project-scoped (`local`) server, and walks through the code that produces each line of output.

## Extend the running example

```bash
cat > /tmp/mcp-triage-demo.json <<'EOF'
{
  "mcpServers": {
    "docs-search": {
      "type": "stdio",
      "command": "python3",
      "args": ["docs_search_server.py", "--serve"],
      "env": {"DOCS_ROOT": "/home/me/docs"}
    },
    "weather-api": {
      "type": "http",
      "url": "https://weather.example.com/mcp",
      "headers": {"Authorization": "Bearer replace-me"}
    }
  },
  "projects": {
    "/home/me/kotlin-app": {
      "mcpServers": {
        "gradle-tools": {
          "command": "node",
          "args": ["gradle-mcp/index.js"]
        }
      }
    }
  }
}
EOF
python3 scripts/mcp_inventory.py --path /tmp/mcp-triage-demo.json
```

Real output:

```
3 configurable MCP server(s) (user/local scope):

  docs-search  [user, stdio]  python3
  weather-api  [user, http]  https://weather.example.com/mcp
  gradle-tools  [local, stdio]  node

# disable (removes it — effective next session):
  claude mcp remove docs-search --scope user
  claude mcp remove weather-api --scope user
  claude mcp remove gradle-tools --scope local

# restore (copy these FIRST — removing loses the config):
  claude mcp add docs-search --scope user -e DOCS_ROOT=/home/me/docs -- python3 docs_search_server.py --serve
  claude mcp add --transport http weather-api https://weather.example.com/mcp --header 'Authorization: Bearer replace-me' --scope user
  claude mcp add gradle-tools --scope local -- node gradle-mcp/index.js
```

## What's happening in the code (`scripts/mcp_inventory.py`)

**`load_config(path)`** — one line of real work: `json.loads(Path(path).read_text())`, wrapped in a
`try/except Exception: return {}`. This is why a missing file or malformed JSON never crashes the
tool; it just behaves like an empty config (see chapter 00's troubleshooting note).

**`collect_servers(config)`** — walks two shapes out of the config dict:
- `config["mcpServers"]` → each entry becomes a **`user`**-scope server. This is `docs-search` and
  `weather-api` above.
- `config["projects"][<any path>]["mcpServers"]` → each entry becomes a **`local`**-scope server.
  This is `gradle-tools`, nested under `/home/me/kotlin-app` in the fixture — the project path
  itself isn't part of the output, only the servers configured under it.

  This maps exactly onto how `claude mcp add` actually writes config: `--scope user` writes to the
  top-level `mcpServers`, `--scope local` writes to the current project's entry under `projects`.
  That's *why* the restore commands say `--scope user` / `--scope local` respectively — it's not a
  guess, it's read back from wherever the server was actually found.

**`_restore_cmd(name, cfg, scope, transport)`** — the interesting function, because it has to
reconstruct a command that *creates* a server from data that only describes one that already
exists:
- For `transport == "stdio"`: rebuilds `-e KEY=value` for every entry in `cfg["env"]`, then
  `command` + `args` joined and shell-quoted (via `shlex.quote`), placed after a literal `--`. See
  `docs-search`'s restore line: `-e DOCS_ROOT=/home/me/docs -- python3 docs_search_server.py --serve`.
- For anything else (`http`, or any other named transport): rebuilds `--header 'Key: value'` for
  every entry in `cfg["headers"]`, and uses `cfg["url"]` instead of command/args. See
  `weather-api`'s restore line: `--transport http weather-api https://weather.example.com/mcp
  --header 'Authorization: Bearer replace-me'`.

Everything is `shlex.quote`d, so values with spaces or shell metacharacters round-trip safely —
that's exercised directly by `test_http_restore` in `tests/test_inventory.py` (the header value
`"Bearer z"` has a space in it and still comes back quoted correctly).

## The `--json` form — what the `/mcp-triage:triage` command actually consumes

The human table above is for a person reading a terminal. The command
(`commands/triage.md`, covered in [chapter 04](04-the-triage-workflow.md)) reads the
structured form instead:

```bash
python3 scripts/mcp_inventory.py --path /tmp/mcp-triage-demo.json --json
```

```json
[
  {
    "name": "docs-search",
    "scope": "user",
    "transport": "stdio",
    "where": "python3",
    "remove": "claude mcp remove docs-search --scope user",
    "restore": "claude mcp add docs-search --scope user -e DOCS_ROOT=/home/me/docs -- python3 docs_search_server.py --serve"
  },
  {
    "name": "weather-api",
    "scope": "user",
    "transport": "http",
    "where": "https://weather.example.com/mcp",
    "remove": "claude mcp remove weather-api --scope user",
    "restore": "claude mcp add --transport http weather-api https://weather.example.com/mcp --header 'Authorization: Bearer replace-me' --scope user"
  },
  {
    "name": "gradle-tools",
    "scope": "local",
    "transport": "stdio",
    "where": "node",
    "remove": "claude mcp remove gradle-tools --scope local",
    "restore": "claude mcp add gradle-tools --scope local -- node gradle-mcp/index.js"
  }
]
```

Same data, same three servers — this is what a `Read`/`Bash`-capable assistant parses to build a
Keep/Disable table without re-implementing any of the reconstruction logic itself.

## What never shows up here — and why that's structural, not a promise

Notice the fixture above has no claude.ai connectors (Drive/Gmail/Calendar) and no plugin-provided
servers in it, and none appear in the output. That's not because `collect_servers` filters them
out — it's because it never looks anywhere they'd be. The function only ever reads two keys,
`mcpServers` and `projects.*.mcpServers`. Connector and plugin config live elsewhere in Claude
Code's internals and this script has no code path that touches them. So "mcp-triage only advises on
servers you can actually `claude mcp remove`" (from `README.md`) isn't a policy the script chooses
to follow — it's the only thing the script is *capable* of seeing.

## Verify your build

```bash
python3 -m pytest tests/test_inventory.py -q -k restore
```

Expected: `2 passed` (`test_stdio_restore_reconstructs_command_args_env` and
`test_http_restore`). Then re-run the `--path` command above yourself and confirm your output
matches the block shown here byte-for-byte (only the fixture's file path or any values you change
should differ).

Next: [02 — The catalog](02-the-catalog.md) leaves MCP servers behind and picks up the other half of
the tool — probing *every* installed extension (skills, plugins, agents, commands, servers) and
working out which of them are actually switched on right now.
