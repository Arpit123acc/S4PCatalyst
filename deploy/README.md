# Deploy — rebuilding the S4PC Catalyst delivery host

Everything needed to bring the host back from nothing. Nothing here contains a secret.

## Why this folder exists

The configuration that makes the pipeline work used to live only in `ec2-user`'s home
directory, outside version control. In particular `~/.claude/settings.json` — the file that
routes Claude Code to **Amazon Bedrock** — was unversioned, so a rebuilt box would have
silently lost Bedrock routing with no record of what it had been.

## How inference is authenticated (important)

The pipeline does **not** use an Anthropic account or an `ANTHROPIC_API_KEY`. Claude Code
runs with `CLAUDE_CODE_USE_BEDROCK=1`, so all inference goes to Amazon Bedrock using the
**EC2 IAM instance profile**. Verified empirically:

| Test | Result |
|---|---|
| `claude -p` with AWS creds and no Anthropic login | works |
| `claude -p` with the IAM instance profile blocked | `Could not load credentials from any providers` |

There is no `~/.claude/.credentials.json` and no `oauthAccount` on this host. Consequence:
**a disabled personal Anthropic account does not affect the pipeline.** The only credential
dependency is AWS. (Commercial entitlement to run Claude Code is a separate contractual
matter — not a technical dependency of this host.)

## Contents

| File | Purpose |
|---|---|
| `ecosystem.config.js` | PM2 definitions for `s4pc-webapp` (8321) and `s4pc-mcp` (3002) |
| `claude-settings.json` | The Bedrock routing config to install at `~/.claude/settings.json` |
| `bootstrap.sh` | Installs the above and starts the services |

## Process supervision

PM2, not systemd — the host already had PM2 with `pm2-ec2-user.service` **enabled**, so it
already survived reboot and restarted crashed processes. Adding systemd for two more services
would have put two supervisors on one box. `pm2 list` is the single source of truth.

| Service | Port | Supervisor | Notes |
|---|---|---|---|
| `digital-brain` (DigitalBrainS3) | 3001 | PM2 (pre-existing) | Node; slated for retirement |
| `s4pc-mcp` | 3002 | PM2 | 25 tools; Claude Code connects as `context7` |
| `s4pc-webapp` | 8321 | PM2 | Pipeline UI + engine |

## Rebuild

```bash
git clone <repo> /home/ec2-user/s4pc
cd /home/ec2-user/s4pc
bash deploy/bootstrap.sh
```

Then confirm:

```bash
pm2 list                      # all three services online
curl -s localhost:8321 >/dev/null && echo UI ok
claude -p 'Reply with exactly: BEDROCK_OK' --strict-mcp-config --mcp-config /dev/null
```

## Why the Claude Code version is pinned

`DISABLE_AUTOUPDATER=1` and `autoUpdates: false`. The CLI previously self-updated
(2.1.197 → 2.1.247 on 2026-08-27) with no change control. `webapp/app.py` parses the CLI's
`stream-json` output and depends on `--strict-mcp-config`, `--permission-mode` and
`--allowedTools` behaviour, so an unattended CLI upgrade can break the pipeline. Upgrade
deliberately instead:

```bash
npm install -g @anthropic-ai/claude-code@<version>
# then re-run a pipeline end-to-end before trusting it
```

## The brain's client documents are not in git

`brain/` (raw client docs, masked chunks, FAISS index, ingest logs, OAuth token cache) is
git-ignored and is **not** restored by this bootstrap. Re-run the SharePoint harvest and
`embed_chunks.py` separately. The `GRAPH_*` / `SHAREPOINT_*` credentials that harvest needs
are supplied as environment variables at ingest time only — they are never written to a file
and are not present in any running service's environment.
