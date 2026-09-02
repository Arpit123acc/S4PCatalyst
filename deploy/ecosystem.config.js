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
      env: {
        S4PC_UI_HOST: '0.0.0.0',   // reachable over the SSH tunnel, not just loopback
      },
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
        AWS_REGION: 'us-east-1',   // Bedrock Titan embeddings for search_brain
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
  ],
};
