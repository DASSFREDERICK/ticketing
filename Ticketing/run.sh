#!/bin/bash
# Starts the Helix portal using the settings in .env (no pasting into the terminal).
cd "$(dirname "$0")"
[ -f .env ] || { echo "Missing .env - copy env.example to .env and fill in the token"; exit 1; }
source venv/bin/activate
set -a; source .env; set +a
echo "Token length: ${#ZAMMAD_TOKEN} (should be about 64)"
exec uvicorn main:app --host 0.0.0.0 --port 9000
