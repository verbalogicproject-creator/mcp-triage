"""Probing MCP servers for their tool lists — and staying safe while doing it.

This is the one module that starts a process, so most of these tests are about
restraint: off unless asked, bounded when it runs, silent about secrets, and
never able to turn a broken server into a failed search.
"""

import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mcp_tools


# ── restraint ───────────────────────────────────────────────────────────────

def test_probing_is_off_unless_explicitly_enabled(monkeypatch, tmp_path):
    """Starting every configured server as a side effect of a search would be a
    surprise. It has to be asked for."""
    monkeypatch.delenv("MCP_TRIAGE_PROBE_TOOLS", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(mcp_tools, "probe", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert mcp_tools.tool_map({"s": {"command": "echo"}}, cache_dir=tmp_path) == {}
    assert called["n"] == 0, "nothing may be started when probing is off"


def test_no_servers_means_no_work(tmp_path):
    assert mcp_tools.tool_map({}, cache_dir=tmp_path) == {}


# ── parsing a real server's stdout ──────────────────────────────────────────

def test_tools_are_found_among_interleaved_log_lines():
    """Servers write logs to stdout alongside protocol messages, so the parser
    scans for the response rather than assuming it owns the stream."""
    stream = "\n".join([
        "starting up, loading config...",
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}),
        "[warn] cache miss",
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "search_kg", "description": "Hybrid retrieval over the graph"},
            {"name": "get_doc", "description": "Fetch one document"}]}}),
        "idle",
    ])
    tools = mcp_tools._tools_from_stream(stream)
    assert [t["name"] for t in tools] == ["search_kg", "get_doc"]
    assert tools[0]["description"] == "Hybrid retrieval over the graph"


def test_stream_without_a_tools_response_yields_none():
    assert mcp_tools._tools_from_stream("no json here at all") is None
    assert mcp_tools._tools_from_stream(
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})) is None


def test_malformed_tool_entries_are_skipped_not_crashed():
    stream = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
        {"name": "good", "description": "fine"},
        {"no_name": "bad"},
        "not even a dict",
    ]}})
    assert [t["name"] for t in mcp_tools._tools_from_stream(stream)] == ["good"]


def test_tools_text_flattens_names_and_descriptions():
    text = mcp_tools.tools_text([{"name": "a", "description": "does A"},
                                 {"name": "b", "description": "does B"}])
    assert "a does A" in text and "b does B" in text


# ── talking to an actual subprocess ─────────────────────────────────────────

def _fake_server(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "server.py"
    p.write_text("import sys, json\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_probe_stdio_reads_a_real_servers_tool_list(tmp_path):
    server = _fake_server(tmp_path, (
        "sys.stdin.read()\n"
        "print(json.dumps({'jsonrpc':'2.0','id':1,'result':{}}))\n"
        "print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':["
        "{'name':'ping','description':'checks liveness'}]}}))\n"))
    tools = mcp_tools.probe_stdio({"command": sys.executable, "args": [str(server)]})
    assert tools == [{"name": "ping", "description": "checks liveness"}]


def test_a_hanging_server_is_bounded_by_the_timeout(tmp_path):
    """A server that never answers must cost a timeout, not a wedged search."""
    server = _fake_server(tmp_path, "import time\ntime.sleep(30)\n")
    assert mcp_tools.probe_stdio(
        {"command": sys.executable, "args": [str(server)]}, timeout=1.0) is None


def test_a_crashing_server_degrades_to_none(tmp_path):
    server = _fake_server(tmp_path, "raise SystemExit(3)\n")
    assert mcp_tools.probe_stdio({"command": sys.executable, "args": [str(server)]}) is None


def test_a_missing_command_degrades_to_none():
    assert mcp_tools.probe_stdio({"command": "/definitely/not/here"}) is None
    assert mcp_tools.probe_stdio({}) is None


def test_server_env_is_passed_through_so_it_can_start(tmp_path):
    """env exists to make the server runnable; the test proves it arrives."""
    server = _fake_server(tmp_path, (
        "import os\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':["
        "{'name': os.environ.get('PROBE_MARKER','missing'),'description':'d'}]}}))\n"))
    tools = mcp_tools.probe_stdio({"command": sys.executable, "args": [str(server)],
                                   "env": {"PROBE_MARKER": "arrived"}})
    assert tools[0]["name"] == "arrived"


def test_secrets_are_never_returned_from_a_probe(tmp_path):
    """env goes IN so the server starts; nothing about it comes back out."""
    server = _fake_server(tmp_path, (
        "sys.stdin.read()\n"
        "print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':["
        "{'name':'t','description':'d'}]}}))\n"))
    cfg = {"command": sys.executable, "args": [str(server)],
           "env": {"API_KEY": "SUPERSECRET"}}
    blob = json.dumps(mcp_tools.probe_stdio(cfg))
    assert "SUPERSECRET" not in blob and "API_KEY" not in blob


# ── caching ─────────────────────────────────────────────────────────────────

def test_results_are_cached_so_probing_is_a_one_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TRIAGE_PROBE_TOOLS", "1")
    calls = {"n": 0}

    def counting(_cfg, _timeout=None):
        calls["n"] += 1
        return [{"name": "t", "description": "d"}]

    monkeypatch.setattr(mcp_tools, "probe", counting)
    servers = {"s": {"command": "x"}}
    first = mcp_tools.tool_map(servers, cache_dir=tmp_path)
    assert first["s"]
    mcp_tools.tool_map(servers, cache_dir=tmp_path)
    assert calls["n"] == 1, "second call must read the cache, not re-probe"


def test_a_failing_server_is_cached_as_empty_rather_than_retried(tmp_path, monkeypatch):
    """A server broken now is very likely broken in a minute; re-probing every
    search would pay its timeout every time."""
    monkeypatch.setenv("MCP_TRIAGE_PROBE_TOOLS", "1")
    calls = {"n": 0}

    def failing(_cfg, _timeout=None):
        calls["n"] += 1
        return None

    monkeypatch.setattr(mcp_tools, "probe", failing)
    servers = {"broken": {"command": "x"}}
    assert mcp_tools.tool_map(servers, cache_dir=tmp_path) == {"broken": ""}
    mcp_tools.tool_map(servers, cache_dir=tmp_path)
    assert calls["n"] == 1


def test_changing_a_servers_command_invalidates_the_cache(tmp_path):
    a = mcp_tools._fingerprint({"s": {"command": "one"}})
    b = mcp_tools._fingerprint({"s": {"command": "two"}})
    assert a != b


def test_fingerprint_ignores_env_so_secrets_never_reach_the_cache_key(tmp_path):
    a = mcp_tools._fingerprint({"s": {"command": "x", "env": {"K": "secret1"}}})
    b = mcp_tools._fingerprint({"s": {"command": "x", "env": {"K": "secret2"}}})
    assert a == b


def test_unwritable_cache_dir_still_returns_results(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TRIAGE_PROBE_TOOLS", "1")
    monkeypatch.setattr(mcp_tools, "probe",
                        lambda *a, **k: [{"name": "t", "description": "d"}])
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file")
    assert mcp_tools.tool_map({"s": {"command": "x"}}, cache_dir=blocker)["s"]
