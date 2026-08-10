# Hosting S4PC Catalyst on one shared machine

You don't need a dedicated server. Run **one instance** on any always-on machine your team can
reach (a colleague's workstation, a small internal VM, or a VM in your cloud/BTP account), protect
it with a password, and share the URL. The code, skills, and catalogs then live on **only that one
machine** — teammates just use the browser UI.

## Setup (5 minutes)
On the machine that will host it:

**Windows**
```
set S4PC_ACCESS_PASSWORD=ChooseAStrongPassword
START-HOSTED.cmd
```

**macOS / Linux**
```
export S4PC_ACCESS_PASSWORD='ChooseAStrongPassword'
./start-hosted.sh
```

This binds to the network (`0.0.0.0:8321`) and turns on login. Teammates open
**`http://<this-machine-ip>:8321`** and sign in with user **`team`** and your password.
(Optional: set `S4PC_ACCESS_USER` to change the username, `S4PC_UI_PORT` to change the port.)

Find the machine's IP with `ipconfig` (Windows) / `ifconfig` or `ip addr` (macOS/Linux), and make
sure the port is reachable (same LAN or VPN; open the firewall for that port if needed).

## Prerequisites on the host
- **Python 3.9+** (runs the app).
- **Claude Code**, logged in — the pipeline engine runs under **this host's** Claude session, so
  every teammate's run uses the host's Claude account. Use your **enterprise Claude Code** here.

## Security notes (read before exposing it)
- **Single shared password** (HTTP Basic auth) — fine for a trusted internal team. It is *not*
  per-user; anyone with the password has full access.
- **Use HTTPS for anything beyond a trusted LAN.** Basic auth sends the password base64-encoded
  (not encrypted). For real network exposure, put it behind an internal reverse proxy (nginx / IIS /
  Caddy) that terminates HTTPS, or keep it on a trusted network / VPN only.
- **Keep it off the public internet.** Bind it only where your team can reach it; never port-forward
  it to the open internet.
- **It's still single-user underneath** — one run at a time is the intended model; it's a team
  *viewer/runner*, not a multi-tenant server.
- Stop it by closing the `START-HOSTED` window (or Ctrl+C). `SHUTDOWN.cmd` also works and will send
  the password automatically if `S4PC_ACCESS_PASSWORD` is set in that shell.

## Local single-user mode is unchanged
If you **don't** set `S4PC_ACCESS_PASSWORD`, the app runs open on `127.0.0.1` exactly as before —
no login, local only. Auth only turns on when you set the password.
