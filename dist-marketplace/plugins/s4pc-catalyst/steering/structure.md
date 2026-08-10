# Steering — Project Structure

```
S4PC-Catalyst-v1.0/
├── .claude/
│   ├── agents/       6 subagent roles (Delivery Lead, Extensibility Architect, Developer, …)
│   ├── skills/       4 skills — the playbooks the agents run
│   ├── commands/     slash commands (/run-pipeline, /extensibility, …)
│   ├── steering/     this folder — persistent project context (product / tech / structure)
│   ├── hooks/        clean_core_guard.py (non-blocking PostToolUse guard)
│   ├── settings.json hook configuration
│   └── launch.json   webapp launch config
├── .mcp.json         registers the s4pc MCP governance server
├── CLAUDE.md         non-negotiable clean-core rules (imports the steering docs)
├── mcp-server/       server.py (governance tools) + catalog/ (released APIs/CDS/BAdIs/lint rules)
├── webapp/           app.py (demo UI + pipeline engine) + ui/index.html + data/agents.json
├── workflows/        RICEFW + process workflow references
├── specs/            spec templates (requirements / design / tasks) — see specs/README.md
├── input/            Functional Design documents (the pipeline's entry point)
└── output/<RUN-ID>/  one folder per pipeline run: run.json + 01..10 deliverables
```

**How the pieces relate:** a **skill** is the playbook; an **agent** is a role that runs a skill;
**slash commands** are quick entry points to skills; the **MCP server** provides governed SAP
facts (release checks, lint, advisor); **hooks** add an editor-level clean-core guard; **steering**
+ CLAUDE.md give every session the platform rules. Pipeline runs read/write `output/<RUN-ID>/`.
