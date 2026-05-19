#!/bin/bash
# deploy.sh — push latest cmms.html to the Debian server and rebuild container
# Usage: ./deploy.sh

set -e

SERVER="sully@10.85.2.27"
REMOTE_DIR="~/cmi-cmms"

echo "📦 Copying files to server..."
scp cmms.html nginx.conf Dockerfile docker-compose.yml "$SERVER:$REMOTE_DIR/"

echo "🐳 Rebuilding and restarting container..."
ssh "$SERVER" "cd $REMOTE_DIR && docker compose up -d --build"

echo "✅ Done — CMMS is live at http://cmms.cmi"
