# 02 — The catalog

Chapters 00–01 were about MCP servers: a few entries in one JSON file. This chapter is about the
other half of the tool, which has a harder job — find **every** extension this Claude Code install
can offer, and work out which ones are actually switched on **right now**.

That second half is the part a plain listing can't do. A skill sitting inside a disabled plugin is
on disk, readable, complete — and completely invisible to your session. If the catalog reported it
as available you'd be told to use something you can't.

## A new running example: a fake Claude home

`catalog.py` reads a Claude *home* directory, so the fixture is a directory tree rather than a
single file. This one has two plugins from one marketplace — `notes` (enabled, two skills) and
`deploy` (disabled, one skill) — plus a usage counter on one skill.

```bash
D=/tmp/mcp-triage-demo-home
rm -rf "$D"
mkdir -p "$D/.claude/plugins/cache/demo-market/notes/1.0.0/skills/note-search" \
         "$D/.claude/plugins/cache/demo-market/notes/1.0.0/skills/note-export" \
         "$D/.claude/plugins/cache/demo-market/deploy/1.0.0/skills/ship-it" "$D/proj"
for p in notes deploy; do echo '{}' > "$D/.claude/plugins/cache/demo-market/$p/1.0.0/plugin.json"; done

printf -- '---\nname: note-search\ndescription: Search your markdown notes by keyword and tag.\n---\nFinds notes across a vault.\n' \
  > "$D/.claude/plugins/cache/demo-market/notes/1.0.0/skills/note-search/SKILL.md"
printf -- '---\nname: note-export\ndescription: Export notes to HTML or PDF.\n---\nExports a note vault.\n' \
  > "$D/.claude/plugins/cache/demo-market/notes/1.0.0/skills/note-export/SKILL.md"
printf -- '---\nname: ship-it\ndescription: Deploy a service to production and watch the rollout.\n---\nDeploys and monitors.\n' \
  > "$D/.claude/plugins/cache/demo-market/deploy/1.0.0/skills/ship-it/SKILL.md"

printf '{"enabledPlugins":{"notes@demo-market":true,"deploy@demo-market":false}}' > "$D/.claude/settings.json"
printf '{"skillUsage":{"notes:note-search":{"usageCount":42,"lastUsedAt":1}}}' > "$D/.claude.json"
printf '{"version":2,"plugins":{"notes@demo-market":[{"scope":"user","installPath":"%s/.claude/plugins/cache/demo-market/notes/1.0.0"}],"deploy@demo-market":[{"scope":"user","installPath":"%s/.claude/plugins/cache/demo-market/deploy/1.0.0"}]}}' "$D" "$D" \
  > "$D/.claude/plugins/installed_plugins.json"
```

Run the catalog against it:

```bash
python3 scripts/catalog.py --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj
```

Real output (verbatim from an actual run):

```
5 extension(s) found — 3 available now, 2 switched off.
Scanned: /tmp/mcp-triage-demo-home

  plugin       2 total     1 available
  skill        3 total     2 available
```

Five records from three files: two plugins, and the three skills they ship. Three are available
(the `notes` plugin and its two skills); two are off (the `deploy` plugin and `ship-it`).

Your run will print one or more extra `Note: another Claude install exists at …` lines. That is not
noise — see "More than one Claude home" below.

## One record, in full

```bash
python3 scripts/catalog.py --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj --json
```

The record for the switched-off skill:

```json
{
  "id": "skill:deploy:ship-it",
  "name": "deploy:ship-it",
  "kind": "skill",
  "scope": "plugin",
  "plugin": "deploy@demo-market",
  "enabled": 0,
  "state_reason": "plugin disabled",
  "description": "Deploy a service to production and watch the rollout.",
  "body": "Deploys and monitors.",
  "path": "/tmp/mcp-triage-demo-home/.claude/plugins/cache/demo-market/deploy/1.0.0/skills/ship-it/SKILL.md",
  "enable_cmd": "set \"deploy@demo-market\": true in enabledPlugins (or run /plugin)",
  "disable_cmd": "set \"deploy@demo-market\": false in enabledPlugins (or run /plugin)",
  "usage_count": 0,
  "last_used_at": 0,
  "root": "/tmp/mcp-triage-demo-home"
}
```

Four fields carry most of the weight:

- **`path`** — the file this came from. It is what makes a suggestion checkable: open it and judge
  for yourself. A recommendation you can't verify isn't worth much.
- **`enabled` + `state_reason`** — not "does this exist" but "can this session use it, and if not,
  why not". Here: the skill's file is fine; its *plugin* is off.
- **`enable_cmd`** — note it names the **plugin**, not the skill. You don't switch on a skill. This
  matters again in [chapter 03](03-ranking.md), where it shapes how results cluster.
- **`root`** — which Claude home this came from. See below.

## Resolving "is it on?"

`enabled` is computed, not read from one place. Three things feed it:

1. **The settings cascade**, weakest first: `~/.claude/settings.json` → `.claude/settings.json` →
   `.claude/settings.local.json`. Later scopes win per key, which is how Claude Code itself resolves
   them — a project-local `false` beats a user-level `true`. Getting this backwards would advertise
   plugins the session can't see.
2. **Plugin state inherits down.** A skill, command, or agent inside a disabled plugin is
   unavailable regardless of its own file.
3. **`skillOverrides`** can switch off an individual skill inside an *enabled* plugin
   (`state_reason: "skill turned off"`).

## Two things the catalog deliberately does not do

**It never reads MCP `env` or `headers` values.** This output is built to be printed, ranked, and
pasted into a session; credentials must not ride along. Only `mcp_inventory.py`
([chapter 01](01-inventory-and-restore.md)) touches them, because a restore command genuinely needs
them. `tests/test_catalog.py` locks this with a fixture containing a fake secret and asserts it
never appears in the output.

**It never invents a path.** A plugin's recorded `installPath` can be stale — Claude Code may
record `.../unknown` while the real directory is named after the commit sha. So the recorded path is
treated as a *candidate*: candidates are tried in descending trustworthiness and the first one that
actually contains a plugin manifest wins. If none does, `resolve_plugin_root` returns `None` and the
plugin is reported as unresolved:

```
12 plugin(s) had no directory on disk (reported, not guessed):
  - github@claude-plugins-official
  …
```

That is real output from the machine this was written on. Those twelve are installed at *project*
scope for a different project and were never fetched here. Saying so is more useful than emitting a
path that isn't there.

## The MCP blind spot, and the one probe that fixes it

Every other extension carries its own text. A skill has a description and a prompt body; a plugin
has a manifest. An MCP server has a command and some args:

```
MCP server (stdio) — python3
```

Twenty-eight characters, and the only distinguishing token is an interpreter name. Measured, MCP
servers averaged **44 characters** of searchable text against thousands for a skill, which made
them findable only when a query happened to echo the server's own name. For a tool that started
life as MCP triage, that was the worst-covered kind in its own index.

The tool names and descriptions do exist — inside the *running* server. Nothing on disk has them
and no CLI prints them (`claude mcp get` reports status and command, not tools). So the only
honest source is to ask the server, over the MCP protocol it already speaks:

```bash
MCP_TRIAGE_PROBE_TOOLS=1 python3 scripts/catalog.py
python3 scripts/mcp_tools.py     # or probe directly, to see what each server offers
```

Real output from this machine:

```
  context-os-dev       2 tool(s)  contextos_map Return a context-os map…
  game_engine         41 tool(s)  ask_can_i Capability lookup: 'can I do X?'…
  gemini-coder         8 tool(s)  search_kg Hybrid retrieval over the Gemini KG-RAG…
  stitch              13 tool(s)  create_project Creates a new Stitch project…
```

Average searchable text per server: **44 → ~2,000 characters**. "Generate UI screens from a
design" now returns the `stitch` server, which previously never surfaced at all.

Two servers came back with zero tools. That is reported as zero rather than retried — see below.

### Why it is opt-in

This is the only code in the repo that starts a process. Everything else reads files. A search
that silently spawned every configured server would be a surprise, so it happens only when asked,
and the run is bounded in three ways:

- a **hard per-server timeout** — a server that never answers costs a timeout, not a wedged search;
- **results are cached**, keyed on what each server runs, so probing is a one-off;
- **failures are cached as empty**, deliberately. A server broken now is very likely broken in a
  minute, and re-probing it on every search would pay its timeout every single time.

`env` and `headers` are passed *in* so a server can start, and never come back out — not returned,
not cached, and not part of the cache key. `test_mcp_tools.py` plants a fake credential and
asserts it appears nowhere in the output.

## More than one Claude home

Some machines run more than one Claude Code install — the one this was written on has two, a PRoot
Ubuntu distro and Termux, each with its own `~/.claude.json`, its own plugins, and its own on/off
state.

A probe that expands `~` and stops silently reports one install's inventory as the whole picture.
So the scan root is explicit, every record is stamped with its `root`, and any *other* home found on
the machine is reported as unscanned rather than folded in:

```
Note: another Claude install exists at /data/data/com.termux/files/home and was not scanned.
      Its extensions are not available to this session.
      Add it with:  --home /data/data/com.termux/files/home
```

Merging them would be worse than missing them. A plugin enabled in the other install is not
available to this session, so "it's on, go ahead" would be false, and an enable command for it
would be something you cannot act on from where you're sitting.

To scan both, pass `--home` twice. Records keep their separate `root` values and same-named
extensions stay distinct rows — they genuinely are two installs.

## Verify your build

With the fixture above in place:

```bash
python3 scripts/catalog.py --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj \
  | head -6
```

Expected: the 5/3/2 summary block shown above. Then confirm the state resolution is real rather
than assumed — flip the disabled plugin on and watch the counts move:

```bash
printf '{"enabledPlugins":{"notes@demo-market":true,"deploy@demo-market":true}}' \
  > /tmp/mcp-triage-demo-home/.claude/settings.json
python3 scripts/catalog.py --home /tmp/mcp-triage-demo-home --project /tmp/mcp-triage-demo-home/proj | head -1
```

Expected: `5 extension(s) found — 5 available now, 0 switched off.` Set it back to `false` before
the next chapter, which uses the disabled state to demonstrate ranking.

Next: [03 — Ranking](03-ranking.md) takes this catalog and matches it against a task description.
