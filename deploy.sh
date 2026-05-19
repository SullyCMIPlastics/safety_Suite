#!/bin/bash
# deploy.sh — push latest changes to the Debian server
# Usage:
#   ./deploy.sh          → copy cmms.html only (instant, no rebuild)
#   ./deploy.sh --full   → rebuild Docker image too (needed after main.py / requirements changes)

set -e

SERVER="sully@10.85.2.27"
REMOTE_DIR="~/cmi-cmms"

echo "📦 Copying cmms.html to server..."
scp cmms.html "$SERVER:$REMOTE_DIR/"

if [[ "$1" == "--full" ]]; then
  echo "🐳 Copying all files and rebuilding container..."
  scp main.py requirements.txt Dockerfile docker-compose.yml nginx.conf "$SERVER:$REMOTE_DIR/" 2>/dev/null || \
  scp main.py requirements.txt Dockerfile docker-compose.yml "$SERVER:$REMOTE_DIR/"
  ssh "$SERVER" "cd $REMOTE_DIR && docker compose up -d --build"
  echo "✅ Full rebuild complete — CMMS is live at http://cmms.cmi"
else
  echo "✅ Done — changes are live at http://cmms.cmi (no restart needed)"
fi
