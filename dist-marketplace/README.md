# S4PC Catalyst — S/4HANA Cloud Public Edition clean-core delivery

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
