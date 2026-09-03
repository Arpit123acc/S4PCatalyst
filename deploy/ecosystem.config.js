// PM2 process definitions for the S4PC Catalyst delivery host.
//
// Why PM2 and not systemd: the host already runs PM2 with `pm2-ec2-user.service`
// enabled, so it already survives reboot and restarts crashed processes. Adding
// systemd for these two would mean two supervisors on one box — worse operationally
// than one. `pm2 list` stays the single place to see what is running.
//
// Usage (see bootstrap.sh):
//   pm2 start deploy/ecosystem.config.js
//   pm2 save                # persist to ~/.pm2/dump.pm2 so reboot restores it
//
// No secrets belong in this file — it is version-controlled. Anything sensitive
// (SAP communication user, GRAPH_*/SHAREPOINT_* for brain ingest) stays an
// environment variable supplied out-of-band, never committed.

module.exports = {
  apps: [
    {
      // The pipeline UI + engine (spawns `claude -p` per phase).
      name: 's4pc-webapp',
      cwd: '/home/ec2-user/s4pc',
      script: 'webapp/app.py',
      interpreter: 'python3.11',
      // No S4PC_UI_HOST override: app.py defaults to 127.0.0.1 and must stay there.
      // An SSH tunnel does not need a wildcard bind — `-L 8321:localhost:8321` resolves
      // its target on this host, so loopback serves it. Binding 0.0.0.0 published the
      // pipeline UI to everything that could route to this box.
      env: {},
      autorestart: true,
      max_restarts: 10,
      min_uptime: '30s',           // a crash inside 30s counts toward max_restarts
      restart_delay: 5000,
      max_memory_restart: '1G',
      out_file: '/home/ec2-user/.pm2/logs/s4pc-webapp-out.log',
      error_file: '/home/ec2-user/.pm2/logs/s4pc-webapp-err.log',
      time: true,
    },
    {
      // The unified governance + brain MCP server over HTTP (25 tools), which
      // Claude Code connects to as `context7` through the SSH tunnel.
      name: 's4pc-mcp',
      cwd: '/home/ec2-user/s4pc',
      script: 'mcp-server/server.py',
      args: '--http 3002',
      interpreter: 'python3.11',
      env: {
        S4PC_MODE: 'offline',
        AWS_REGION: 'us-east-1',       // Bedrock Titan embeddings for search_brain
        // Layer 2 (semantic_search) embeds via Bedrock Titan rather than a local
        // sentence-transformers model: this host has 3.7 GB RAM and already holds the
        // FAISS index in this process, so a resident PyTorch model would risk the 2G
        // max_memory_restart. Rebuild with the same value set:
        //   S4PC_VECTOR_BACKEND=bedrock python3.11 mcp-server/vector/build_index.py
        S4PC_VECTOR_BACKEND: 'bedrock',
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: '30s',
      restart_delay: 5000,
      max_memory_restart: '2G',    // FAISS index is held in memory
      out_file: '/home/ec2-user/.pm2/logs/s4pc-mcp-out.log',
      error_file: '/home/ec2-user/.pm2/logs/s4pc-mcp-err.log',
      time: true,
    },
    {
      // Read-only visualisation of the brain corpus (brain-ui/README.md). Deliberately
      // a separate process from s4pc-webapp so a demo surface can never reach the
      // pipeline's approval controls, and so restarting one does not touch the other.
      // Binds loopback: it has no authentication — see docs/brain-endpoint-setup.md
      // before exposing it beyond the SSH tunnel.
      name: 'brain-ui',
      cwd: '/home/ec2-user/s4pc',
      script: 'brain-ui/server.py',
      args: '--port 8400',
      interpreter: 'python3.11',
      env: {
        AWS_REGION: 'us-east-1',   // Bedrock Titan embeds the search query
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: '30s',
      restart_delay: 5000,
      max_memory_restart: '1G',
      out_file: '/home/ec2-user/.pm2/logs/brain-ui-out.log',
      error_file: '/home/ec2-user/.pm2/logs/brain-ui-err.log',
      time: true,
    },
    {
      // Scheduled brain backup to S3 — NOT a long-running service.
      //
      // Why PM2 and not cron: this host has no cron at all (crond is inactive and
      // crontab is not installed — Amazon Linux 2023 ships systemd timers instead),
      // and adding a systemd timer needs sudo and would put a second scheduler on a
      // box whose stated rule is one supervisor. PM2 is already here and already
      // survives reboot via pm2-ec2-user.service.
      //
      // autorestart:false + cron_restart is the PM2 idiom for a periodic one-shot:
      // PM2 launches it on the schedule, the script exits, and PM2 leaves it stopped
      // until the next tick. A 'stopped' status for this entry is CORRECT, not a fault.
      name: 'brain-backup',
      cwd: '/home/ec2-user/s4pc',
      script: 'scripts/backup_brain.sh',
      interpreter: 'bash',
      cron_restart: '15 2 * * *',       // 02:15 UTC daily
      autorestart: false,
      env: {
        AWS_REGION: 'us-east-1',
      },
      out_file: '/home/ec2-user/.pm2/logs/brain-backup-out.log',
      error_file: '/home/ec2-user/.pm2/logs/brain-backup-err.log',
      time: true,
    },
  ],
};
