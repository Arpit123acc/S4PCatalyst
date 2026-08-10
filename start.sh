#!/bin/sh
# S4PC Catalyst - S/4HANA Public Cloud (macOS/Linux)
# Zero dependencies - needs only Python 3.9+.
cd "$(dirname "$0")"
exec python3 webapp/app.py
