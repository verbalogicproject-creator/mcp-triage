#!/usr/bin/env python3
"""Ask MCP servers what tools they offer, so they can be searched like anything else.

THE GAP THIS CLOSES. Every other extension carries its own text — a skill has a
description and a prompt body, a plugin has a manifest. An MCP server has none.
Its config holds a command and some args, so the catalog could only say:

    MCP server (stdio) — python3

Twenty-eight characters, of which the only distinguishing token is often an
interpreter name. Measured, that made MCP servers effectively unfindable: they
surfaced only when a query happened to echo the server's own name. For a tool
that began life as MCP triage, that was the worst-covered kind in its own index.

The tool names and descriptions exist, but only inside the running server —
nothing on disk has them, and no CLI prints them. So the only way to read them is
to ask the server, over the MCP protocol it already speaks.

WHY THIS IS OPT-IN. Everything else in this repo reads files. This *starts a
process* (or makes a network call), and a probe that spawns every configured
server as a side effect of a search would be a surprise. It is therefore off
unless MCP_TRIAGE_PROBE_TOOLS is set, hard-timeout-bounded per server, and cached
so it is a one-off rather than a per-query cost.

SECRETS. `env` and `headers` are *passed to the server* so it can start, and are
never returned, cached, or stored. Only tool names and descriptions come back.

Read-only with respect to Claude Code config. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Sequence

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 8.0
MAX_TOOLS_TEXT = 4000

DEFAULT_CACHE = Path(os.path.expanduser("~")) / ".cache" / "mcp-triage"


def _requests() -> str:
    """initialize -> initialized -> tools/list, newline-delimited.

    Written in one shot rather than as a back-and-forth: a well-behaved server
    reads its stdin sequentially and answers in order, and sending everything up
    front means the whole exchange fits inside a single `subprocess.run` timeout.
    A conversational implementation would need threads or select loops to get the
    same hard bound, and a hung server is the exact failure mode to guard.
    """
    return "\n".join(json.dumps(m) for m in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-triage", "version": "0.2.1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )) + "\n"


def _tools_from_stream(text: str) -> list[dict] | None:
    """Pull the tools/list result out of a server's stdout.

    Servers interleave logging with protocol output, so this scans every line for
    a JSON-RPC response carrying a tools array rather than assuming the response
    is the whole of stdout or arrives on any particular line.
    """
    found: list[dict] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        tools = (msg.get("result") or {}).get("tools")
        if isinstance(tools, list):
            found = tools
    if found is None:
        return None
    out = []
    for t in found:
        if isinstance(t, dict) and t.get("name"):
            out.append({"name": str(t["name"]),
                        "description": str(t.get("description") or "")})
    return out


def probe_stdio(cfg: dict, timeout: float = DEFAULT_TIMEOUT) -> list[dict] | None:
    command = cfg.get("command")
    if not command:
        return None
    argv = [command, *(cfg.get("args") or [])]
    # The server's own env is needed to start it; it is never returned.
    env = {**os.environ, **{str(k): str(v) for k, v in (cfg.get("env") or {}).items()}}
    try:
        proc = subprocess.run(argv, input=_requests(), env=env, timeout=timeout,
                              capture_output=True, text=True, check=False)
    except Exception:
        return None
    return _tools_from_stream(proc.stdout or "")


def probe_http(cfg: dict, timeout: float = DEFAULT_TIMEOUT) -> list[dict] | None:
    url = cfg.get("url")
    if not url:
        return None
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    headers.update({str(k): str(v) for k, v in (cfg.get("headers") or {}).items()})
    body = json.dumps({"jsonrpc": "2.0", "id": 2,
                       "method": "tools/list", "params": {}}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _tools_from_stream(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def probe(cfg: dict, timeout: float = DEFAULT_TIMEOUT) -> list[dict] | None:
    transport = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
    return probe_http(cfg, timeout) if transport in ("http", "sse") else probe_stdio(cfg, timeout)


def tools_text(tools: Sequence[dict]) -> str:
    """Flatten a tool list into searchable text."""
    parts = [f"{t.get('name','')} {t.get('description','')}".strip() for t in tools]
    return " · ".join(p for p in parts if p)[:MAX_TOOLS_TEXT]


# ── caching ─────────────────────────────────────────────────────────────────

def _fingerprint(servers: dict[str, dict]) -> str:
    h = hashlib.blake2b(digest_size=16)
    for name in sorted(servers):
        cfg = servers[name] or {}
        # Identity of a server for cache purposes: what it runs, never its secrets.
        h.update(name.encode("utf-8"))
        h.update(str(cfg.get("command") or cfg.get("url") or "").encode("utf-8"))
        h.update(json.dumps(cfg.get("args") or [], sort_keys=True).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _cache_dir() -> Path:
    return Path(os.environ.get("MCP_TRIAGE_EMBED_CACHE", str(DEFAULT_CACHE)))


def tool_map(servers: dict[str, dict], timeout: float = DEFAULT_TIMEOUT,
             cache_dir: Path | None = None, force: bool = False) -> dict[str, str]:
    """{server name -> searchable tool text}, cached. {} when probing is off.

    A server that fails to answer is cached as empty rather than retried on every
    run: a server that is broken or slow now is very likely still broken in a
    minute, and the alternative is paying its timeout on every single search.
    """
    if not servers:
        return {}
    if not force and not os.environ.get("MCP_TRIAGE_PROBE_TOOLS"):
        return {}
    cdir = Path(cache_dir) if cache_dir else _cache_dir()
    cache = cdir / f"tools-{_fingerprint(servers)}.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: str(v) for k, v in data.items()}
        except Exception:
            pass

    out: dict[str, str] = {}
    for name, cfg in servers.items():
        tools = probe(cfg or {}, timeout)
        out[name] = tools_text(tools) if tools else ""
    _write_cache(cdir, cache, out)
    return out


def _write_cache(cdir: Path, cache: Path, data: dict[str, str]) -> None:
    """Best-effort: an unwritable cache costs a re-probe, never an answer."""
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        shutil.move(str(tmp), str(cache))
        for old in cdir.glob("tools-*.json"):
            if old != cache:
                old.unlink(missing_ok=True)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    cfg_path = Path(args[args.index("--path") + 1]) if "--path" in args \
        else Path(os.path.expanduser("~")) / ".claude.json"
    try:
        servers = (json.loads(cfg_path.read_text(encoding="utf-8")).get("mcpServers")) or {}
    except Exception:
        servers = {}
    if not servers:
        print("No user-scope MCP servers found.")
        return 0
    print(f"Probing {len(servers)} server(s) — this starts each one briefly.\n")
    got = tool_map(servers, force=True)
    for name in sorted(servers):
        text = got.get(name, "")
        n = len(text.split(" · ")) if text else 0
        print(f"  {name:18s} {n:3d} tool(s)  {text[:70]}{'…' if len(text) > 70 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
