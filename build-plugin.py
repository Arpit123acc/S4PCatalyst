#!/usr/bin/env python3
"""
build-plugin.py — regenerate the distributable Claude Code plugin marketplace from
the canonical sources in this repo.

WHY THIS EXISTS
    The local web app uses `.claude/` + `mcp-server/` at the repo root. Teammates
    instead consume a *plugin* (added by URL, run in their own Claude Code under their
    own licence). A plugin is copied to each teammate's cache as a SELF-CONTAINED unit,
    so it cannot reference this repo's `.claude/`. This script assembles that
    self-contained copy into `dist-marketplace/` — the ONLY folder you push to GitHub.
    Your webapp code and client FDs in `input/` stay out of the shared repo.

WHEN TO RUN
    After editing anything under `.claude/` (skills, agents, commands, hooks, steering)
    or `mcp-server/`, run:  python build-plugin.py   then commit + push dist-marketplace/.
    One source of truth (`.claude/`), no manual double-editing.

OUTPUT (push this folder to your private GitHub repo)
    dist-marketplace/
      README.md                         teammate one-pager
      .gitignore
      .claude-plugin/marketplace.json
      plugins/s4pc-catalyst/
        .claude-plugin/plugin.json
        skills/ agents/ commands/ hooks/ steering/ mcp-server/ CLAUDE.md
"""
import os, re, json, shutil

REPO = os.path.dirname(os.path.abspath(__file__))
SRC_CLAUDE = os.path.join(REPO, ".claude")
SRC_MCP = os.path.join(REPO, "mcp-server")
CLAUDE_MD = os.path.join(REPO, "CLAUDE.md")

PLUGIN_NAME = "s4pc-catalyst"
MARKETPLACE_NAME = "s4pc-tools"
VERSION = "1.0.0"
OWNER = "S4PC Catalyst Team"          # <- edit to your team name if you like

DIST = os.path.join(REPO, "dist-marketplace")
PLUG = os.path.join(DIST, "plugins", PLUGIN_NAME)
MKT_DIR = os.path.join(DIST, ".claude-plugin")

JUNK_EXT = (".pyc", ".code-workspace")
JUNK_DIR = {"__pycache__", "logs"}
# Generated at runtime on each machine (db.py auto-migrates catalog.db from the JSON seeds;
# build_index.py / build_graph.py regenerate index.json / graph.json). Ship the engines and the
# JSON seeds, never the generated data — the plugin rebuilds them on first use.
JUNK_FILES = {"catalog.db", "index.json", "index.npy", "graph.json"}

def _ignore(_dir, names):
    return [n for n in names if n in JUNK_DIR or n in JUNK_FILES or n.endswith(JUNK_EXT)]

def _copytree(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=_ignore)

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

# ---------------------------------------------------------------- 1. clean slate
if os.path.isdir(DIST):
    shutil.rmtree(DIST)
os.makedirs(PLUG)

# ---------------------------------------------------------------- 2. copy content
_copytree(os.path.join(SRC_CLAUDE, "skills"),   os.path.join(PLUG, "skills"))
_copytree(os.path.join(SRC_CLAUDE, "agents"),   os.path.join(PLUG, "agents"))
_copytree(os.path.join(SRC_CLAUDE, "commands"), os.path.join(PLUG, "commands"))
_copytree(os.path.join(SRC_CLAUDE, "steering"), os.path.join(PLUG, "steering"))
_copytree(SRC_MCP,                              os.path.join(PLUG, "mcp-server"))

os.makedirs(os.path.join(PLUG, "hooks"), exist_ok=True)
shutil.copy2(os.path.join(SRC_CLAUDE, "hooks", "clean_core_guard.py"),
             os.path.join(PLUG, "hooks", "clean_core_guard.py"))

shutil.copy2(CLAUDE_MD, os.path.join(PLUG, "CLAUDE.md"))

# ---------------------------------------------------------------- 3. embed rules
# A plugin does not carry the repo's CLAUDE.md / @steering imports, so inline the
# binding platform rules into the pipeline skill body to preserve clean-core fidelity.
with open(CLAUDE_MD, encoding="utf-8") as fh:
    claude_md = fh.read()
m = re.search(r"(## Non-negotiable platform rules.*?)(?=\n## Deliverable standards)",
              claude_md, re.S)
rules_block = m.group(1).strip() if m else ""

pipeline_skill = os.path.join(PLUG, "skills", "s4pc-ricefw-pipeline", "SKILL.md")
if rules_block and os.path.isfile(pipeline_skill):
    with open(pipeline_skill, encoding="utf-8") as fh:
        body = fh.read()
    banner = (
        "\n> **Binding platform rules (S/4HANA Cloud Public Edition).** These travel with this "
        "plugin in place of the workspace `CLAUDE.md`; treat them as hard constraints for every "
        "step below.\n\n" + rules_block + "\n\n---\n"
    )
    fm = re.match(r"^(---\n.*?\n---\n)", body, re.S)
    body = (fm.group(1) + banner + body[fm.end():]) if fm else (banner + body)
    _write(pipeline_skill, body)

# ---------------------------------------------------------------- 4. fix MCP path
for root, _dirs, files in os.walk(os.path.join(PLUG, "skills")):
    for f in files:
        if f.endswith(".md"):
            p = os.path.join(root, f)
            with open(p, encoding="utf-8") as fh:
                t = fh.read()
            t2 = re.sub(r"(?<!\$\{CLAUDE_PLUGIN_ROOT\}/)mcp-server/", "${CLAUDE_PLUGIN_ROOT}/mcp-server/", t)
            if t2 != t:
                _write(p, t2)

# ---------------------------------------------------------------- 5. hooks.json
_write(os.path.join(PLUG, "hooks", "hooks.json"), json.dumps({
    "hooks": {"PostToolUse": [{"matcher": "Write|Edit", "hooks": [
        {"type": "command",
         "command": "python \"${CLAUDE_PLUGIN_ROOT}/hooks/clean_core_guard.py\""}]}]}
}, indent=2) + "\n")

# ---------------------------------------------------------------- 6. plugin.json
_write(os.path.join(PLUG, ".claude-plugin", "plugin.json"), json.dumps({
    "name": PLUGIN_NAME,
    "description": ("S/4HANA Cloud Public Edition clean-core delivery toolkit — "
                    "run /run-pipeline, /create-fd, /extensibility, /clean-core-review "
                    "with released-object governance, gates and human checkpoints."),
    "version": VERSION,
    "author": {"name": OWNER},
    "mcpServers": {"s4pc": {"command": "python",
                            "args": ["${CLAUDE_PLUGIN_ROOT}/mcp-server/server.py"],
                            "env": {"S4PC_MODE": "offline"}}}
}, indent=2) + "\n")

# ---------------------------------------------------------------- 7. marketplace
_write(os.path.join(MKT_DIR, "marketplace.json"), json.dumps({
    "name": MARKETPLACE_NAME,
    "owner": {"name": OWNER},
    "description": "Accenture S/4HANA Cloud Public Edition clean-core delivery tools.",
    "plugins": [{"name": PLUGIN_NAME, "source": "./plugins/" + PLUGIN_NAME,
                 "description": ("Clean-core RICEFW pipeline, extensibility decisions, "
                                 "clean-core reviews and FD creation for S/4HANA Cloud Public Edition."),
                 "version": VERSION, "author": {"name": OWNER}}]
}, indent=2) + "\n")

# ---------------------------------------------------------------- 8. teammate docs
_write(os.path.join(DIST, ".gitignore"),
       "__pycache__/\n*.pyc\nlogs/\n.DS_Store\n"
       "*.db\nplugins/*/mcp-server/vector/index.json\nplugins/*/mcp-server/vector/index.npy\n"
       "plugins/*/mcp-server/graph/graph.json\n")
_write(os.path.join(DIST, "README.md"), """# S4PC Catalyst — S/4HANA Cloud Public Edition clean-core delivery

A Claude Code **plugin**. Run the governed RICEFW pipeline, extensibility decisions,
clean-core reviews and FD creation inside your **own** Claude Code, on your own
enterprise licence.

## Prerequisites
- **Claude Code** — in the Claude Desktop app, the CLI, or the VS Code / JetBrains
  extension. It's included in your Claude enterprise licence. (This does **not** run in
  the claude.ai web chat.)
- **Logged in:** run `/login` once if prompted.
- **Read access to this repo** (ask the owner to add you) and GitHub auth on your
  machine — `gh auth login`, or an SSH key loaded in your agent.
- Python 3.9+ on PATH is optional (used by the offline governance server; the pipeline
  still runs without it).

## Install (once)
In Claude Code:
```
/plugin marketplace add Arpit123acc/S4PCatalyst
/plugin install s4pc-catalyst@s4pc-tools
```
Choose **User** scope to use it in any project.

## Use
```
/run-pipeline        Run the 12-step clean-core pipeline on a Functional Design
/create-fd           Draft a Functional Design from business inputs
/extensibility       Decide key-user / developer / side-by-side per requirement
/clean-core-review   Adversarial clean-core review of code or a design
```
Commands may appear namespaced, e.g. `s4pc-catalyst:run-pipeline`. The pipeline stops
at three human checkpoints (solution, design, acceptance) and waits for your decision.

## Get updates
```
/plugin marketplace update s4pc-tools
```
(or turn on auto-update in `/plugin` → Marketplaces)

## Good to know
- **Governance:** objects are checked against released-object catalogs. If your org
  blocks MCP servers, the checks fall back to naming heuristics + the SAP Help lists —
  the pipeline still runs end to end.
- **Tokens:** usage is billed to **your** enterprise seat; your admin sees team totals
  in the Claude enterprise Console.
""")

# ---------------------------------------------------------------- docs
# bundle repo docs (e.g. the BTP deploy runbook) so teammates get them with the plugin
src_docs = os.path.join(REPO, "docs")
if os.path.isdir(src_docs):
    _copytree(src_docs, os.path.join(DIST, "docs"))

# ---------------------------------------------------------------- report
skills = len([d for d in os.listdir(os.path.join(PLUG, "skills"))
              if os.path.isdir(os.path.join(PLUG, "skills", d))])
agents = len([f for f in os.listdir(os.path.join(PLUG, "agents")) if f.endswith(".md")])
cmds = len([f for f in os.listdir(os.path.join(PLUG, "commands")) if f.endswith(".md")])
print("Built marketplace at:", os.path.relpath(DIST, REPO))
print("  push THIS folder to your private GitHub repo.")
print("  skills:", skills, "| agents:", agents, "| commands:", cmds,
      "| rules embedded:", bool(rules_block))
