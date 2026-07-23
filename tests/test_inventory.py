"""The inventory parser: reconstruct correct remove + restore commands from ~/.claude.json config."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mcp_inventory


def test_stdio_restore_reconstructs_command_args_env():
    cfg = {"mcpServers": {"gem": {"type": "stdio", "command": "python3",
                                   "args": ["srv.py", "--serve"], "env": {"KEY": "v"}}}}
    servers = mcp_inventory.collect_servers(cfg)
    assert len(servers) == 1
    s = servers[0]
    assert s["name"] == "gem" and s["scope"] == "user" and s["transport"] == "stdio"
    assert s["remove"] == "claude mcp remove gem --scope user"
    assert "claude mcp add gem --scope user" in s["restore"]
    assert "-e KEY=v" in s["restore"]
    assert "-- python3 srv.py --serve" in s["restore"]


def test_http_restore():
    cfg = {"mcpServers": {"api": {"type": "http", "url": "https://x/mcp",
                                  "headers": {"Authorization": "Bearer z"}}}}
    s = mcp_inventory.collect_servers(cfg)[0]
    assert s["transport"] == "http"
    assert "--transport http api https://x/mcp" in s["restore"]
    assert "--header 'Authorization: Bearer z'" in s["restore"]


def test_local_scope_from_projects():
    cfg = {"projects": {"/some/proj": {"mcpServers": {"loc": {"command": "node", "args": ["s.js"]}}}}}
    s = mcp_inventory.collect_servers(cfg)[0]
    assert s["scope"] == "local"
    assert "--scope local" in s["remove"] and "--scope local" in s["restore"]


def test_empty_config_yields_no_servers():
    assert mcp_inventory.collect_servers({}) == []
    assert mcp_inventory.collect_servers({"mcpServers": {}}) == []
