#!/usr/bin/env python3
"""Probe every extension this Claude Code install can offer — plugins, skills,
commands, agents, MCP servers — and emit one normalized record each.

WHY THIS EXISTS. Asking a model "which plugin fits this task?" makes it *recall*
names, and recall invents things that aren't installed. This script makes the
answer a *retrieval* problem instead: nothing can be suggested unless it was
found on disk here, with a path to prove it. Every record carries `path`, so a
suggestion is falsifiable — you can open the file it came from.

It also answers the question a plain listing can't: **is it switched on right
now?** A skill inside a disabled plugin is invisible to the session even though
its file is right there, so `enabled` is resolved through the real settings
cascade (user < project < local) rather than assumed.

Read-only: this parses config and reads extension files. It never writes config.
Stdlib-only, offline.

SECRETS. MCP server `env` and `headers` values are deliberately NOT read or
carried. This catalog is built to be printed, ranked, and pasted into a session;
credentials must never ride along. (`mcp_inventory.py` reads them because a
restore command genuinely needs them — that stays there, not here.)

Usage:
    python3 catalog.py                 # human summary
    python3 catalog.py --json          # structured records
    python3 catalog.py --project DIR   # resolve project scope against DIR (default: cwd)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable

HOME = Path(os.path.expanduser("~"))

# THIS DEVICE HAS MORE THAN ONE CLAUDE HOME. A Termux install and a PRoot/chroot
# install each carry a full, independent config tree — own ~/.claude.json, own
# plugins, own skills, own transcripts. A probe that expands "~" and stops has
# quietly reported one root's inventory as the whole picture.
#
# So: the scan root is explicit, every record is stamped with the `root` it came
# from, and any *other* Claude home found here is reported as unscanned rather
# than folded in. Folding them in would be worse than missing them — a plugin
# enabled in the other environment is not available to this session, and
# "enable it" would be advice you cannot act on from here.
HOME_HINTS = (
    "/root",
    "/data/data/com.termux/files/home",
)

# A body longer than this is truncated before indexing. Bodies are prompt text —
# a few of them run to tens of KB, and the tail adds noise, not recall.
MAX_BODY_CHARS = 8000

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_KV_RE = re.compile(r"\A([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)\Z")


# ── plumbing ────────────────────────────────────────────────────────────────

def load_json(path: str | Path) -> dict:
    """Parse a JSON file, or return {} if it is missing/unreadable/invalid.

    A broken settings file is silently ignored by Claude Code itself, so the
    catalog matches that behaviour rather than crashing on it.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split leading `---` YAML frontmatter into a flat {key: value} dict + body.

    Deliberately a minimal scalar parser, not YAML: extension frontmatter is
    flat `key: value` in practice, and a real YAML dep would break the
    stdlib-only rule. Nested/list values are skipped rather than guessed at.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t", "-"):  # nested block or list item — skip
            continue
        km = _KV_RE.match(line.strip())
        if not km:
            continue
        val = km.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        meta[km.group(1)] = val
    return meta, m.group(2)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _clip(s: str, n: int = MAX_BODY_CHARS) -> str:
    s = " ".join(s.split())
    return s[:n]


# ── settings cascade ────────────────────────────────────────────────────────

def settings_files(project_dir: Path, home: Path) -> list[Path]:
    """Cascade order, weakest first: user < project < local."""
    return [
        home / ".claude" / "settings.json",
        project_dir / ".claude" / "settings.json",
        project_dir / ".claude" / "settings.local.json",
    ]


def other_claude_homes(home: Path) -> list[Path]:
    """Claude homes on this device that are NOT the one being scanned.

    Reported, never merged — see the HOME_HINTS note. Extra roots can be named
    explicitly with --home.
    """
    return [
        p for h in HOME_HINTS
        if (p := Path(h)) != home and (p / ".claude.json").is_file()
    ]


def resolve_cascade(project_dir: Path, home: Path) -> dict[str, dict]:
    """Merge the settings cascade for the two keys this catalog needs.

    Later scopes override earlier ones per key, which is how Claude Code itself
    resolves them — a project `false` beats a user `true`.
    """
    enabled_plugins: dict[str, bool] = {}
    skill_overrides: dict[str, str] = {}
    for f in settings_files(project_dir, home):
        cfg = load_json(f)
        for k, v in (cfg.get("enabledPlugins") or {}).items():
            if isinstance(v, bool):
                enabled_plugins[k] = v
        for k, v in (cfg.get("skillOverrides") or {}).items():
            if isinstance(v, str):
                skill_overrides[k] = v
    return {"enabledPlugins": enabled_plugins, "skillOverrides": skill_overrides}


# ── plugin root resolution ──────────────────────────────────────────────────

def _plugin_manifest_dir(p: Path) -> bool:
    return (p / ".claude-plugin" / "plugin.json").is_file() or (p / "plugin.json").is_file()


def resolve_plugin_root(
    key: str,
    installed: dict,
    marketplaces: dict,
    cache_root: Path | None = None,
) -> Path | None:
    """Best-effort filesystem root for a `<name>@<marketplace>` plugin key.

    `installed_plugins.json` records an `installPath`, but it can be stale (it
    may name a `.../unknown` directory while the real one is the commit sha), so
    the recorded path is a *candidate*, not the answer. Candidates are tried in
    descending trustworthiness and the first one that actually holds a plugin
    manifest wins. Returns None when nothing resolves — the caller reports that
    honestly instead of emitting a path that isn't there.
    """
    name, _, marketplace = key.partition("@")
    cache_root = cache_root or (HOME / ".claude" / "plugins" / "cache")
    candidates: list[Path] = []

    for entry in (installed.get("plugins") or {}).get(key, []) or []:
        if not isinstance(entry, dict):
            continue
        ip = entry.get("installPath")
        if ip:
            candidates.append(Path(ip))
            sha = entry.get("gitCommitSha")
            if sha:
                candidates.append(Path(ip).parent / str(sha)[:12])
            ver = entry.get("version")
            if ver and ver != "unknown":
                candidates.append(Path(ip).parent / str(ver))

    # Any versioned directory under the marketplace's cache folder.
    cache_dir = cache_root / marketplace / name
    if cache_dir.is_dir():
        try:
            candidates.extend(sorted(cache_dir.iterdir(), key=lambda p: p.name))
        except OSError:
            pass

    # Directory-backed marketplaces: either <path>/plugins/<name> or <path> itself
    # (a marketplace may point straight at a single plugin directory).
    src = (marketplaces.get(marketplace) or {}).get("source") or {}
    mpath = src.get("path")
    if mpath:
        candidates.append(Path(mpath) / "plugins" / name)
        candidates.append(Path(mpath))

    for c in candidates:
        try:
            if c.is_dir() and _plugin_manifest_dir(c):
                return c
        except OSError:
            continue
    return None


# ── record construction ─────────────────────────────────────────────────────

def _record(**kw: Any) -> dict:
    """One catalog row. Every field is a scalar so it can go straight into SQLite."""
    base = {
        "id": "", "name": "", "kind": "", "scope": "", "plugin": "",
        "enabled": 0, "state_reason": "", "description": "", "body": "",
        "path": "", "enable_cmd": "", "disable_cmd": "",
        "usage_count": 0, "last_used_at": 0, "root": "",
    }
    base.update(kw)
    return base


def _usage_for(names: Iterable[str], usage: dict) -> tuple[int, int]:
    """Highest (count, last_used) across candidate keys.

    Skills nested in a directory are recorded under either the qualified
    `<dir>:<name>` or the bare `<name>`, so both are checked before a counter is
    called zero.
    """
    count, last = 0, 0
    for n in names:
        e = usage.get(n)
        if isinstance(e, dict):
            count = max(count, int(e.get("usageCount") or 0))
            last = max(last, int(e.get("lastUsedAt") or 0))
    return count, last


def collect_from_plugin_dir(root: Path, plugin_key: str, plugin_on: bool,
                            skill_usage: dict, overrides: dict) -> list[dict]:
    """Skills, commands, and agents shipped inside one plugin directory."""
    out: list[dict] = []
    short = plugin_key.partition("@")[0]

    for sk in sorted(root.glob("skills/*/SKILL.md")):
        meta, body = parse_frontmatter(read_text(sk))
        bare = meta.get("name") or sk.parent.name
        qual = f"{short}:{bare}"
        count, last = _usage_for((qual, bare), skill_usage)
        off = overrides.get(qual) == "off" or overrides.get(bare) == "off"
        out.append(_record(
            id=f"skill:{qual}", name=qual, kind="skill", scope="plugin",
            plugin=plugin_key, enabled=int(plugin_on and not off),
            state_reason=("skill turned off" if off else
                          "available" if plugin_on else "plugin disabled"),
            description=meta.get("description", ""), body=_clip(body), path=str(sk),
            enable_cmd=_plugin_enable_cmd(plugin_key),
            disable_cmd=_plugin_disable_cmd(plugin_key),
            usage_count=count, last_used_at=last,
        ))

    for cm in sorted(root.glob("commands/*.md")):
        meta, body = parse_frontmatter(read_text(cm))
        bare = cm.stem
        qual = f"{short}:{bare}"
        count, last = _usage_for((qual, bare), skill_usage)
        out.append(_record(
            id=f"command:{qual}", name=f"/{qual}", kind="command", scope="plugin",
            plugin=plugin_key, enabled=int(plugin_on),
            state_reason="available" if plugin_on else "plugin disabled",
            description=meta.get("description", ""), body=_clip(body), path=str(cm),
            enable_cmd=_plugin_enable_cmd(plugin_key),
            disable_cmd=_plugin_disable_cmd(plugin_key),
            usage_count=count, last_used_at=last,
        ))

    for ag in sorted(root.glob("agents/*.md")):
        meta, body = parse_frontmatter(read_text(ag))
        if not meta.get("name"):
            continue  # no name in frontmatter => co-located doc, not an agent
        out.append(_record(
            id=f"agent:{short}:{meta['name']}", name=meta["name"], kind="agent",
            scope="plugin", plugin=plugin_key, enabled=int(plugin_on),
            state_reason="available" if plugin_on else "plugin disabled",
            description=meta.get("description", ""), body=_clip(body), path=str(ag),
            enable_cmd=_plugin_enable_cmd(plugin_key),
            disable_cmd=_plugin_disable_cmd(plugin_key),
        ))
    return out


def _plugin_enable_cmd(key: str) -> str:
    return f'set "{key}": true in enabledPlugins (or run /plugin)'


def _plugin_disable_cmd(key: str) -> str:
    return f'set "{key}": false in enabledPlugins (or run /plugin)'


def collect_loose(dirs: list[tuple[Path, str]], skill_usage: dict,
                  overrides: dict) -> list[dict]:
    """Skills/commands/agents that live outside any plugin (user or project dirs)."""
    out: list[dict] = []
    for base, scope in dirs:
        for sk in sorted(base.glob("skills/*/SKILL.md")):
            meta, body = parse_frontmatter(read_text(sk))
            bare = meta.get("name") or sk.parent.name
            count, last = _usage_for((bare,), skill_usage)
            off = overrides.get(bare) == "off"
            out.append(_record(
                id=f"skill:{scope}:{bare}", name=bare, kind="skill", scope=scope,
                enabled=int(not off),
                state_reason="skill turned off" if off else "available",
                description=meta.get("description", ""), body=_clip(body),
                path=str(sk),
                enable_cmd=f'remove "{bare}" from skillOverrides',
                disable_cmd=f'set "{bare}": "off" in skillOverrides',
                usage_count=count, last_used_at=last,
            ))
        for cm in sorted(base.glob("commands/*.md")):
            meta, body = parse_frontmatter(read_text(cm))
            count, last = _usage_for((cm.stem,), skill_usage)
            out.append(_record(
                id=f"command:{scope}:{cm.stem}", name=f"/{cm.stem}", kind="command",
                scope=scope, enabled=1, state_reason="available",
                description=meta.get("description", ""), body=_clip(body),
                path=str(cm), usage_count=count, last_used_at=last,
            ))
        for ag in sorted(base.glob("agents/*.md")):
            meta, body = parse_frontmatter(read_text(ag))
            if not meta.get("name"):
                continue
            out.append(_record(
                id=f"agent:{scope}:{meta['name']}", name=meta["name"], kind="agent",
                scope=scope, enabled=1, state_reason="available",
                description=meta.get("description", ""), body=_clip(body), path=str(ag),
            ))
    return out


def collect_mcp(claude_json: dict, project_dir: Path, project_mcp: dict,
                tools: dict[str, str] | None = None) -> list[dict]:
    """MCP servers from user scope, project-entry scope, and `.mcp.json`.

    Never reads `env` or `headers` — see the module docstring.

    `tools` maps a server name to searchable text describing what it offers (see
    mcp_tools.py). Without it a server's only text is its transport and command,
    which is far too thin to match a task description against — an MCP server
    would be findable only when a query happened to echo its name.
    """
    tools = tools or {}
    out: list[dict] = []
    proj = (claude_json.get("projects") or {}).get(str(project_dir)) or {}
    disabled = set(proj.get("disabledMcpServers") or [])
    disabled_json = set(proj.get("disabledMcpjsonServers") or [])

    def add(name: str, cfg: dict, scope: str, off: bool, enable_cmd: str,
            disable_cmd: str) -> None:
        if not isinstance(cfg, dict):
            return
        transport = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
        # `where` is a command name or URL — never credentials.
        where = cfg.get("command", "") if transport == "stdio" else cfg.get("url", "")
        tool_text = tools.get(name, "")
        out.append(_record(
            id=f"mcp:{scope}:{name}", name=name, kind="mcp", scope=scope,
            enabled=int(not off),
            state_reason="disabled for this project" if off else "connected",
            description=f"MCP server ({transport}) — {where}",
            # The tools ARE the server's description as far as matching goes.
            body=_clip(tool_text), path=str(where),
            enable_cmd=enable_cmd, disable_cmd=disable_cmd,
        ))

    for name, cfg in (claude_json.get("mcpServers") or {}).items():
        add(name, cfg, "user", name in disabled,
            f"/mcp enable {shlex.quote(name)}", f"/mcp disable {shlex.quote(name)}")
    for name, cfg in (proj.get("mcpServers") or {}).items():
        add(name, cfg, "local", name in disabled,
            f"/mcp enable {shlex.quote(name)}", f"/mcp disable {shlex.quote(name)}")
    for name, cfg in (project_mcp.get("mcpServers") or {}).items():
        add(name, cfg, "project", name in disabled_json,
            f'remove "{name}" from disabledMcpjsonServers',
            f'add "{name}" to disabledMcpjsonServers in .claude/settings.local.json')
    return out


def build_catalog(project_dir: Path | None = None,
                  claude_json_path: Path | None = None,
                  home: Path | None = None,
                  mcp_tools: dict[str, str] | None = None) -> list[dict]:
    """Every extension ONE Claude home can offer, with live on/off state.

    Scans exactly one root. Multi-root devices call this once per home and
    concatenate — see `build_multi` — so no record's provenance is ambiguous.
    """
    project_dir = Path(project_dir or Path.cwd()).resolve()
    home = Path(home or HOME)
    claude_json = load_json(claude_json_path or (home / ".claude.json"))
    cascade = resolve_cascade(project_dir, home)
    enabled_plugins = cascade["enabledPlugins"]
    overrides = cascade["skillOverrides"]
    skill_usage = claude_json.get("skillUsage") or {}
    plugin_usage = claude_json.get("pluginUsage") or {}

    installed = load_json(home / ".claude" / "plugins" / "installed_plugins.json")
    mkts = load_json(home / ".claude" / "plugins" / "known_marketplaces.json")
    marketplaces = mkts.get("marketplaces") if isinstance(mkts.get("marketplaces"), dict) else mkts

    records: list[dict] = []

    # Plugins, and everything each one ships.
    for key in sorted(set(enabled_plugins) | set((installed.get("plugins") or {}))):
        on = bool(enabled_plugins.get(key, False))
        root = resolve_plugin_root(key, installed, marketplaces,
                                   cache_root=home / ".claude" / "plugins" / "cache")
        count, last = _usage_for((key,), plugin_usage)
        records.append(_record(
            id=f"plugin:{key}", name=key, kind="plugin", scope="user",
            plugin=key, enabled=int(on),
            state_reason=("enabled" if on else
                          "installed but disabled" if key in enabled_plugins else
                          "installed, not enabled here"),
            description=_plugin_description(root),
            body="", path=str(root) if root else "",
            enable_cmd=_plugin_enable_cmd(key), disable_cmd=_plugin_disable_cmd(key),
            usage_count=count, last_used_at=last,
        ))
        if root:
            records.extend(collect_from_plugin_dir(root, key, on, skill_usage, overrides))

    records.extend(collect_loose(
        [(home / ".claude", "user"), (project_dir / ".claude", "project")],
        skill_usage, overrides,
    ))
    if mcp_tools is None:
        try:
            import mcp_tools as _mt
            proj_entry = (claude_json.get("projects") or {}).get(str(project_dir)) or {}
            mcp_tools = _mt.tool_map({**(claude_json.get("mcpServers") or {}),
                                      **(proj_entry.get("mcpServers") or {})})
        except Exception:
            mcp_tools = {}
    records.extend(collect_mcp(claude_json, project_dir,
                               load_json(project_dir / ".mcp.json"), mcp_tools))

    # Stable order, and de-duplicate ids (a plugin reachable by two roots).
    seen: set[str] = set()
    unique: list[dict] = []
    for r in sorted(records, key=lambda r: (r["kind"], r["id"])):
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        r["root"] = str(home)
        unique.append(r)
    return unique


def build_multi(project_dir: Path | None = None,
                homes: list[Path] | None = None) -> list[dict]:
    """Catalog across several Claude homes, each record stamped with its root.

    Ids are namespaced by root so the same plugin installed in both environments
    stays two distinct rows — they genuinely are two installs, with independent
    on/off state, and collapsing them would make one root's state stand in for
    the other's.
    """
    homes = homes or [HOME]
    out: list[dict] = []
    for h in homes:
        for r in build_catalog(project_dir, home=h):
            if len(homes) > 1:
                r["id"] = f"{h.name}:{r['id']}"
            out.append(r)
    return out


def _plugin_description(root: Path | None) -> str:
    if not root:
        return ""
    for p in (root / ".claude-plugin" / "plugin.json", root / "plugin.json"):
        d = load_json(p).get("description")
        if isinstance(d, str):
            return d
    return ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project = Path(args[args.index("--project") + 1]) if "--project" in args else Path.cwd()

    homes = [Path(args[i + 1]) for i, a in enumerate(args) if a == "--home"] or [HOME]
    rows = build_multi(project, homes)

    if "--json" in args:
        print(json.dumps(rows, indent=2))
        return 0

    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    on = sum(1 for r in rows if r["enabled"])
    scanned = ", ".join(str(h) for h in homes)
    print(f"{len(rows)} extension(s) found — {on} available now, {len(rows) - on} switched off.")
    print(f"Scanned: {scanned}\n")
    for kind in sorted(by_kind):
        items = by_kind[kind]
        live = sum(1 for r in items if r["enabled"])
        print(f"  {kind:9s} {len(items):4d} total  {live:4d} available")

    unresolved = [r for r in rows if r["kind"] == "plugin" and not r["path"]]
    if unresolved:
        print(f"\n{len(unresolved)} plugin(s) had no directory on disk (reported, not guessed):")
        for r in unresolved:
            print(f"  - {r['name']}")

    # A second install on this device is a fact worth stating: its extensions are
    # real but NOT reachable from this session.
    for other in other_claude_homes(homes[0]):
        if other not in homes:
            print(f"\nNote: another Claude install exists at {other} and was not scanned.")
            print("      Its extensions are not available to this session.")
            print(f"      Add it with:  --home {other}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
