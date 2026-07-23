#!/usr/bin/env python3
"""Read Claude Code's configured MCP servers and emit, per server, the exact remove + restore
commands — so /mcp-triage can advise disabling servers a task doesn't need WITHOUT ever losing
their config (Claude Code has no reversible disable for user/local-scope servers; only remove).

Read-only: this parses config, it never writes it. Stdlib-only, offline.

Usage:
    python3 mcp_inventory.py                 # human table + remove/restore commands per server
    python3 mcp_inventory.py --json          # structured JSON
    python3 mcp_inventory.py --path FILE     # parse a specific config (default ~/.claude.json)
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import List


def load_config(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _restore_cmd(name: str, cfg: dict, scope: str, transport: str) -> str:
    if transport == "stdio":
        env = cfg.get("env") or {}
        env_flags = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in env.items())
        parts = [cfg.get("command", "")] + list(cfg.get("args") or [])
        argstr = " ".join(shlex.quote(p) for p in parts if p != "")
        return (
            f"claude mcp add {shlex.quote(name)} --scope {scope} "
            + (env_flags + " " if env_flags else "")
            + "-- " + argstr
        ).strip()
    headers = cfg.get("headers") or {}
    hdr = " ".join(f"--header {shlex.quote(f'{k}: {v}')}" for k, v in headers.items())
    return (
        f"claude mcp add --transport {transport} {shlex.quote(name)} {shlex.quote(cfg.get('url', ''))} "
        + (hdr + " " if hdr else "")
        + f"--scope {scope}"
    ).strip()


def collect_servers(config: dict) -> List[dict]:
    """Return [{name, scope, transport, where, remove, restore}, …] for user + local-scope servers.

    (claude.ai connectors and plugin-provided servers are NOT here — they're managed separately.)
    """
    servers: List[dict] = []

    def add(name: str, cfg: dict, scope: str) -> None:
        if not isinstance(cfg, dict):
            return
        transport = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
        where = cfg.get("command", "") if transport == "stdio" else cfg.get("url", "")
        servers.append({
            "name": name,
            "scope": scope,
            "transport": transport,
            "where": where,
            "remove": f"claude mcp remove {shlex.quote(name)} --scope {scope}",
            "restore": _restore_cmd(name, cfg, scope, transport),
        })

    for name, cfg in (config.get("mcpServers") or {}).items():
        add(name, cfg, "user")
    for proj in (config.get("projects") or {}).values():
        if isinstance(proj, dict):
            for name, cfg in (proj.get("mcpServers") or {}).items():
                add(name, cfg, "local")
    return servers


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = os.path.expanduser("~/.claude.json")
    if "--path" in args:
        path = args[args.index("--path") + 1]

    servers = collect_servers(load_config(path))

    if "--json" in args:
        print(json.dumps(servers, indent=2))
        return 0

    if not servers:
        print("No user/local-scope MCP servers found in ~/.claude.json.")
        print("(claude.ai connectors and plugin-provided servers are managed separately.)")
        return 0

    print(f"{len(servers)} configurable MCP server(s) (user/local scope):\n")
    for s in servers:
        print(f"  {s['name']}  [{s['scope']}, {s['transport']}]  {s['where']}")
    print("\n# disable (removes it — effective next session):")
    for s in servers:
        print(f"  {s['remove']}")
    print("\n# restore (copy these FIRST — removing loses the config):")
    for s in servers:
        print(f"  {s['restore']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
