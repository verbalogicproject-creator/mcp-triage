"""The catalog probe: does it report what is actually installed and switched on?

These lock the properties that make a suggestion trustworthy — correct on/off
state through the settings cascade, honest provenance, no secrets, and no
invented paths. Everything runs against temp fixtures; no test reads the
developer's real config.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import catalog


# ── frontmatter ─────────────────────────────────────────────────────────────

def test_frontmatter_splits_meta_from_body():
    meta, body = catalog.parse_frontmatter(
        "---\nname: thing\ndescription: does a thing\n---\nBody text here.\n"
    )
    assert meta == {"name": "thing", "description": "does a thing"}
    assert body.strip() == "Body text here."


def test_frontmatter_strips_quotes_and_skips_nested_blocks():
    meta, _ = catalog.parse_frontmatter(
        '---\nname: "quoted"\ntools:\n  - Read\n  - Grep\nmodel: sonnet\n---\nbody\n'
    )
    assert meta["name"] == "quoted"
    assert meta["model"] == "sonnet"
    assert "tools" in meta  # the key is seen; its list items are not guessed at
    assert "- Read" not in json.dumps(meta)


def test_no_frontmatter_returns_whole_text_as_body():
    meta, body = catalog.parse_frontmatter("just a file\n")
    assert meta == {} and body == "just a file\n"


def test_load_json_tolerates_missing_and_broken_files(tmp_path):
    assert catalog.load_json(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert catalog.load_json(bad) == {}


# ── settings cascade ────────────────────────────────────────────────────────

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def test_local_scope_overrides_user_scope_for_plugin_state(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"p@m": True}})
    _write(proj / ".claude" / "settings.local.json", {"enabledPlugins": {"p@m": False}})
    cascade = catalog.resolve_cascade(proj, home)
    # user < project < local — the local `false` must win, or the catalog would
    # advertise a plugin the session cannot see.
    assert cascade["enabledPlugins"]["p@m"] is False


def test_skill_overrides_merge_across_scopes(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    _write(home / ".claude" / "settings.json", {"skillOverrides": {"a": "off"}})
    _write(proj / ".claude" / "settings.local.json", {"skillOverrides": {"b": "off"}})
    got = catalog.resolve_cascade(proj, home)["skillOverrides"]
    assert got == {"a": "off", "b": "off"}


# ── plugin root resolution ──────────────────────────────────────────────────

def test_stale_install_path_falls_back_to_commit_sha_dir(tmp_path):
    """installPath can name a directory that was never created (version
    'unknown' while the real dir is the commit sha). The recorded path is a
    candidate, not the answer."""
    cache = tmp_path / "cache"
    real = cache / "mkt" / "plug" / "abc123def456"
    (real / ".claude-plugin").mkdir(parents=True)
    (real / ".claude-plugin" / "plugin.json").write_text("{}")
    installed = {"plugins": {"plug@mkt": [{
        "installPath": str(cache / "mkt" / "plug" / "unknown"),
        "gitCommitSha": "abc123def456789",
    }]}}
    got = catalog.resolve_plugin_root("plug@mkt", installed, {}, cache_root=cache)
    assert got == real


def test_unresolvable_plugin_returns_none_rather_than_a_guess(tmp_path):
    got = catalog.resolve_plugin_root("ghost@nowhere", {"plugins": {}}, {},
                                      cache_root=tmp_path)
    assert got is None


def test_directory_marketplace_root_resolves(tmp_path):
    mkt = tmp_path / "mymarket"
    plug = mkt / "plugins" / "thing"
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / ".claude-plugin" / "plugin.json").write_text("{}")
    marketplaces = {"mymarket": {"source": {"source": "directory", "path": str(mkt)}}}
    got = catalog.resolve_plugin_root("thing@mymarket", {"plugins": {}}, marketplaces,
                                      cache_root=tmp_path / "empty")
    assert got == plug


# ── extension collection + state ────────────────────────────────────────────

def _plugin_with_skill(root: Path, skill_name: str) -> None:
    d = root / "skills" / skill_name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: does {skill_name}\n---\nbody about {skill_name}\n"
    )


def test_skill_in_disabled_plugin_is_reported_unavailable(tmp_path):
    root = tmp_path / "plug"
    _plugin_with_skill(root, "alpha")
    recs = catalog.collect_from_plugin_dir(root, "plug@mkt", plugin_on=False,
                                           skill_usage={}, overrides={})
    skill = next(r for r in recs if r["kind"] == "skill")
    assert skill["enabled"] == 0
    assert skill["state_reason"] == "plugin disabled"
    # The enable path must name the PLUGIN — you cannot switch on a lone skill.
    assert "plug@mkt" in skill["enable_cmd"]


def test_skill_usage_matches_qualified_or_bare_key(tmp_path):
    root = tmp_path / "plug"
    _plugin_with_skill(root, "alpha")
    qualified = catalog.collect_from_plugin_dir(
        root, "plug@mkt", True, {"plug:alpha": {"usageCount": 7, "lastUsedAt": 5}}, {})
    bare = catalog.collect_from_plugin_dir(
        root, "plug@mkt", True, {"alpha": {"usageCount": 3, "lastUsedAt": 2}}, {})
    assert next(r for r in qualified if r["kind"] == "skill")["usage_count"] == 7
    assert next(r for r in bare if r["kind"] == "skill")["usage_count"] == 3


def test_skill_override_off_disables_an_enabled_plugins_skill(tmp_path):
    root = tmp_path / "plug"
    _plugin_with_skill(root, "alpha")
    recs = catalog.collect_from_plugin_dir(root, "plug@mkt", True, {},
                                           {"plug:alpha": "off"})
    skill = next(r for r in recs if r["kind"] == "skill")
    assert skill["enabled"] == 0 and skill["state_reason"] == "skill turned off"


def test_agent_without_name_in_frontmatter_is_skipped(tmp_path):
    root = tmp_path / "plug"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "notes.md").write_text("# just a doc, no frontmatter\n")
    (root / "agents" / "real.md").write_text("---\nname: real\ndescription: d\n---\nbody\n")
    recs = catalog.collect_from_plugin_dir(root, "p@m", True, {}, {})
    names = [r["name"] for r in recs if r["kind"] == "agent"]
    assert names == ["real"]


# ── MCP servers ─────────────────────────────────────────────────────────────

def test_mcp_disabled_state_comes_from_the_project_entry(tmp_path):
    cfg = {
        "mcpServers": {"live": {"command": "x"}, "off": {"command": "y"}},
        "projects": {str(tmp_path): {"disabledMcpServers": ["off"]}},
    }
    recs = catalog.collect_mcp(cfg, tmp_path, {})
    by = {r["name"]: r for r in recs}
    assert by["live"]["enabled"] == 1
    assert by["off"]["enabled"] == 0
    assert by["off"]["state_reason"] == "disabled for this project"


def test_mcp_records_never_carry_env_or_header_values(tmp_path):
    """The catalog is built to be printed and pasted. Credentials must not ride
    along — that is why it reads `command`/`url` and nothing else."""
    cfg = {"mcpServers": {
        "s": {"command": "run", "env": {"API_KEY": "SUPERSECRET"}},
        "h": {"type": "http", "url": "https://x/mcp",
              "headers": {"Authorization": "Bearer SUPERSECRET"}},
    }}
    blob = json.dumps(catalog.collect_mcp(cfg, tmp_path, {}))
    assert "SUPERSECRET" not in blob
    assert "API_KEY" not in blob and "Authorization" not in blob


# ── whole-catalog properties ────────────────────────────────────────────────

def test_empty_environment_yields_no_records(tmp_path):
    rows = catalog.build_catalog(project_dir=tmp_path / "proj",
                                 claude_json_path=tmp_path / "none.json",
                                 home=tmp_path / "home")
    assert rows == []


def test_every_record_is_scalar_valued_for_sqlite(tmp_path):
    """Records go straight into SQLite, so a nested value would fail at insert."""
    home, proj = tmp_path / "home", tmp_path / "proj"
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "a.md").write_text("---\nname: a\ndescription: d\n---\nb\n")
    _write(home / ".claude" / "settings.json", {})
    rows = catalog.build_catalog(proj, tmp_path / "none.json", home=home)
    assert rows, "expected at least the loose agent"
    for r in rows:
        for k, v in r.items():
            assert isinstance(v, (str, int)), f"{k}={v!r} is not scalar"


def test_records_are_stamped_with_the_root_they_came_from(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "a.md").write_text("---\nname: a\ndescription: d\n---\nb\n")
    rows = catalog.build_catalog(proj, tmp_path / "none.json", home=home)
    assert all(r["root"] == str(home) for r in rows)


def test_multi_root_keeps_same_named_extensions_distinct(tmp_path):
    """Two installs are two installs. Collapsing them would let one root's
    on/off state stand in for the other's."""
    proj = tmp_path / "proj"
    homes = []
    for tag in ("one", "two"):
        h = tmp_path / tag
        (h / ".claude" / "agents").mkdir(parents=True)
        (h / ".claude" / "agents" / "same.md").write_text(
            "---\nname: same\ndescription: d\n---\nb\n")
        homes.append(h)
    rows = catalog.build_multi(proj, homes)
    agents = [r for r in rows if r["kind"] == "agent"]
    assert len(agents) == 2
    assert len({r["id"] for r in agents}) == 2
    assert {r["root"] for r in agents} == {str(homes[0]), str(homes[1])}
