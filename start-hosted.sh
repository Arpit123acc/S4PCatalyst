#!/bin/sh
# S4PC Catalyst - SHARED single-instance (one team machine).
# Binds to the network AND requires a password, so teammates use it in the
# browser without a local copy of the code.  Share: http://THIS-MACHINE-IP:8321
cd "$(dirname "$0")"

if [ -z "$S4PC_ACCESS_PASSWORD" ]; then
  echo "[ERROR] Set an access password first, then re-run:"
  echo "        export S4PC_ACCESS_PASSWORD='ChooseAStrongPassword'"
  echo "        ./start-hosted.sh"
  exit 1
fi
: "${S4PC_ACCESS_USER:=team}"
export S4PC_ACCESS_USER
export S4PC_UI_HOST=0.0.0.0
export S4PC_UI_NO_BROWSER=1

echo "Starting S4PC Catalyst (SHARED / password-protected) on port 8321 ..."
echo "Share with the team:  http://THIS-MACHINE-IP:8321   (login user: $S4PC_ACCESS_USER)"
echo "Press Ctrl+C to stop."
exec python3 webapp/app.py
