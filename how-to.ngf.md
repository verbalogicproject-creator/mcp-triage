---
format: ngf/0.0.3
kind: kb_doc
doc_id: mcp-triage-how-to
title: "mcp-triage — how to use it"
version: "0.2.1"
category: tooling
tags:
  - mcp-triage
  - claude-code
  - plugins
  - skills
  - mcp
  - discovery
rag_keywords:
  - which plugin should I use
  - find the right skill for this task
  - what did I install and switch off
  - turn on a disabled plugin
  - too many mcp servers
  - what extensions do I have
audience: eyal_nof
last_updated: 2026-08-11
language: en
direction: ltr
source_workflow: manual-authoring
provenance:
  source_path: how-to.ngf.md
  owner_user: eyal_nof
  repo: mcp-triage
edges:
  command: commands/triage.md
  probe: scripts/catalog.py
  ranker: scripts/triage_rank.py
  engine: declared_core/ (vendored; see VENDORED.json)
---

# mcp-triage — how to use it

## The problem it solves

You install things and forget them. Right now this machine has **191 extensions** —
skills, plugins, MCP servers, agents, commands — and **most are switched off**. A
switched-off extension is invisible: you won't remember it exists, so you'll do the
work by hand instead.

This tells you what you already own that fits the job in front of you.

## One command

```
/mcp-triage:triage <what you're about to work on>
```

Describe the task in plain words. That's the whole interface.

```
/mcp-triage:triage add a voice assistant to a Next.js app
/mcp-triage:triage harden a python repo and set up CI
/mcp-triage:triage design a premium landing page
```

Leave the task off and it infers one from the session — and tells you what it assumed,
so you can correct it.

## What comes back

Four lists. Only the first one usually matters.

| List | Meaning | Do something? |
|---|---|---|
| **Turn on** | Fits this task, but is switched off | **Yes** — this is the point |
| **Already available** | Fits, and is already on | No |
| **In your other install** | Exists, but in your Termux install, not this one | No — can't reach it from here |
| **Idle for this task** | On, but unrelated | Only if you're trimming |

## A real example

Asking for `harden a python repo and set up CI` on this machine returns:

```
Switched off, but relevant — consider turning on:

  nlke-production-auditor@nlke-production-auditor  (+1 more piece(s))
    set "nlke-production-auditor@nlke-production-auditor": true in enabledPlugins (or run /plugin)
      agent    auditor-cicd
               /root/.claude/plugins/cache/nlke-production-auditor/.../agents/auditor-cicd.md
      plugin   nlke-production-auditor@nlke-production-auditor · used 8x
               /root/.claude/plugins/cache/nlke-production-auditor/...
```

Three things to notice:

- It found a **whole audit pipeline** you'd forgotten was installed.
- Results group under **one plugin, one command to run**. You don't switch on a skill —
  you switch on the plugin, and everything it ships comes with it. A plugin's place in
  the list comes from its best match, never from how many pieces it ships.
- Every entry ends in a **file path**. Open it. If a suggestion can't be checked, it
  isn't worth much.

## Acting on it

Copy the `enabledPlugins` line it gives you, or just run `/plugin`. Then **restart** —
config is read at startup, so nothing changes mid-session.

To go the other way and trim, ask it to. For MCP servers it will hand you the
`claude mcp add …` restore line *first*, because removing a server deletes its config
and its saved logins.

## Two things worth knowing

**It can't make things up.** It reads your disk before answering, and can only name what
it actually found there. Ask a normal assistant "which plugin should I use?" and it
answers from memory — sometimes about plugins you've never installed. This doesn't.

**Ranking is relevance first, habit second.** Something you use daily gets a small nudge
up the list, capped at a few places. A tool you've never opened can still be the right
answer for a new task, and a favourite shouldn't bury a better match.

## You have two Claude installs

This device runs two — **PRoot Ubuntu** (`/root`, where this session lives) and
**Termux** (`/data/data/com.termux/files/home`). Each has its own plugins, its own
settings, its own on/off state. Termux currently carries far more switched-on skills.

They're never merged. Anything from the other install is listed separately and marked
unreachable, because handing you an enable command that can't work from where you're
sitting would be worse than staying quiet.

To look across both:

```bash
python3 scripts/catalog.py --home /root --home /data/data/com.termux/files/home
```

## Just browsing

No task, no ranking — what do I have, and what's on?

```bash
python3 scripts/catalog.py          # summary
python3 scripts/catalog.py --json   # structured
```

## It never touches your config

Read-only, always. It prints commands; you decide and run them. It won't enable,
disable, remove, or edit anything on your behalf.
