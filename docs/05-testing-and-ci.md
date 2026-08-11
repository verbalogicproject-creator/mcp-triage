# 05 — Testing and CI

The tool rests on two guarantees, and both are the kind that fail *quietly*:

1. The **restore** command it prints has to work byte-for-byte, or the restore-first safety net
   (chapters 00–01) is a lie.
2. A **suggestion has to correspond to something real** — installed, on disk, and in the state the
   tool claims — or the whole "found, not recalled" premise (chapters 02–03) is a lie.

Nothing about a wrong answer here looks wrong. A fabricated restore line looks like a restore line;
a plausible-but-absent plugin looks like a suggestion. That's what the suite is for.

## The test suite

```bash
python3 -m pytest tests/ -q
```

Real output:

```
............................................                             [100%]
75 passed in 2.09s
```

Five files, split by what they protect:

| File | Tests | Protects |
|---|---|---|
| `tests/test_inventory.py` | 4 | The restore reconstruction (chapter 01) |
| `tests/test_catalog.py` | 19 | Honest probing and state resolution (chapter 02) |
| `tests/test_rank.py` | 21 | Honest ranking (chapter 03) |
| `tests/test_dense.py` | 14 | The semantic booster stays optional (chapter 03) |
| `tests/test_mcp_tools.py` | 17 | The MCP probe stays opt-in and bounded (chapter 02) |

Every test runs against hand-built fixtures or `tmp_path` — **none reads your real config**. That's
both a correctness property (the suite can't pass or fail because of what you happen to have
installed) and the reason the whole thing runs in well under a second.

### The load-bearing tests

Most of the 75 are ordinary coverage. These few are the ones worth knowing by name, because each
locks a claim the tool makes in prose:

| Test | Locks the claim |
|---|---|
| `test_stdio_restore_reconstructs_command_args_env` | "The restore line will actually put it back" — `--scope`, every `-e KEY=value`, and `command` + `args` after a literal `--` |
| `test_local_scope_from_projects` | A project-scoped server gets `--scope local`, matching where `claude mcp add` really writes |
| `test_every_hit_corresponds_to_a_real_catalog_row` | "Found, not recalled" — nothing can be suggested that wasn't probed |
| `test_mcp_records_never_carry_env_or_header_values` | Credentials never enter the catalog; the fixture plants a fake secret and asserts it never appears |
| `test_local_scope_overrides_user_scope_for_plugin_state` | The settings cascade resolves the way Claude Code resolves it, so "available" means available |
| `test_unresolvable_plugin_returns_none_rather_than_a_guess` | A missing plugin directory is reported, never invented |
| `test_lift_never_introduces_something_retrieval_did_not_return` | Usage can reorder what matched, never manufacture a match |
| `test_other_install_never_appears_as_actionable` | An extension in the *other* Claude home is never offered as available or enable-able |
| `test_multi_root_keeps_same_named_extensions_distinct` | Two installs stay two installs; one root's state never stands in for the other's |
| `test_group_order_follows_best_member_not_member_count` | A lean plugin's exact match outranks a large plugin's siblings — plugin size is not relevance |
| `test_no_dense_index_is_byte_identical_to_lexical_only` | The optional booster cannot change the default path at all |
| `test_probing_is_off_unless_explicitly_enabled` | Nothing starts a process unless you asked for it |
| `test_secrets_are_never_returned_from_a_probe` | `env` goes in so a server starts; nothing about it comes back out |

**Three of those exist because the behaviour was wrong first**, which is the most useful thing this
suite can tell you about itself:

- `test_used_extension_outranks_identical_unused_one` failed against the original design and forced
  the bounded lift in [chapter 03](03-ranking.md).
- `test_partition_defaults_the_primary_root_to_the_rows_being_scanned` was written after `--home`
  was found to mark every result unreachable whenever it pointed somewhere other than the session's
  own home.
- `test_group_order_follows_best_member_not_member_count` came from dogfooding, not from unit
  testing: on the real catalog a near-verbatim query ranked its exact match 8th because the
  structural hop was counting plugin size as relevance. The earlier sibling test passed throughout —
  it only asserted that a plugin-mate *appeared*, never that it didn't *outrank* a better match.

A test that never failed hasn't proven much, and a test that asserts presence without asserting
order proves less than it looks like it does.

## CI (`.github/workflows/ci.yml`)

Triggers: every push to `main`, every pull request. One job, `ubuntu-latest`:

```yaml
- name: Unit tests
  run: python -m pytest tests/ -q
- name: Inventory smoke on a fixture config
  run: |
    printf '{"mcpServers":{"a":{"type":"stdio","command":"python3","args":["s.py"]}}}' > /tmp/cfg.json
    python3 scripts/mcp_inventory.py --path /tmp/cfg.json | grep -q "claude mcp add a --scope user -- python3 s.py"
- name: Vendored engine imports with no third-party packages
  run: python3 -c "import declared_core; print(declared_core.__version__)"
- name: Catalog smoke on an empty home (must not crash or invent records)
  run: |
    mkdir -p /tmp/emptyhome /tmp/emptyproj
    python3 scripts/catalog.py --project /tmp/emptyproj --home /tmp/emptyhome --json | grep -q '^\[\]$'
- name: Ranking smoke — a suggestion must trace to a real path
  run: |
    … asserts every hit in every bucket has a non-null path
```

Layered on purpose, because each catches something the layer above can't:

- **Unit tests** call functions directly with hand-built dicts.
- **The inventory smoke** runs the real CLI entry point end to end and greps stdout — it catches a
  regression in `main()`'s printing that a unit test on `collect_servers()` never would.
- **The import check** is the stdlib-only invariant made executable. `declared_core` is vendored
  (see `VENDORED.json`), and vendoring is only safe here because the engine is itself
  dependency-free; if that ever stops being true, this step fails in a bare interpreter.
- **The empty-home smoke** asserts `[]`. An empty environment must produce *nothing*, not a
  best-effort guess — the failure mode this guards is a probe that invents plausible records when it
  finds nothing.
- **The ranking smoke** asserts the property the whole tool is built on: every hit traces to a path.

There is no CI step for the vendored copy's freshness, because that guard lives outside this repo:
`python3 ../tools/revendor.py check --repo mcp-triage` compares the stored tree hash against both the
canonical source and the copy on disk, catching a stale copy *and* a hand-edited one.

## Verify your build

Reproduce CI's own assertions locally, in order.

```bash
python3 -m pytest tests/ -q
```

Expected: `75 passed`.

```bash
printf '{"mcpServers":{"a":{"type":"stdio","command":"python3","args":["s.py"]}}}' > /tmp/cfg.json
python3 scripts/mcp_inventory.py --path /tmp/cfg.json | grep -q "claude mcp add a --scope user -- python3 s.py" && echo PASS
```

Expected: `PASS`. If not, `_restore_cmd`'s stdio branch or `main()`'s print loop has drifted from
what chapters 00–01 walked through.

```bash
mkdir -p /tmp/emptyhome /tmp/emptyproj
python3 scripts/catalog.py --project /tmp/emptyproj --home /tmp/emptyhome --json
```

Expected: exactly `[]`.

```bash
python3 -c "import declared_core; print(declared_core.__version__)"
python3 ../tools/revendor.py check --repo mcp-triage
```

Expected: a version string, then `in sync`. The second command only works from a checkout that sits
alongside the canonical engine repo; from a standalone clone the vendored copy is simply what
shipped.

## Where to go from here

- [`CODEBASE-REPORT.md`](../CODEBASE-REPORT.md) — the module map and data-flow in one place.
- [`README.md`](../README.md) and [`HOW-TO-USE.md`](../HOW-TO-USE.md) — the user-facing quickstart
  and FAQ. [`how-to.ngf.md`](../how-to.ngf.md) is the short intuitive version.
- [`CLAUDE.md`](../CLAUDE.md) — the eight invariants to hold if you're modifying this repo.
