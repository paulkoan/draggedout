#!/usr/bin/env bash
set -euo pipefail

DIR="/opt/data/dragged-out"
DEPLOY_KEY="$DIR/deploy_key"
BUILD_DIR="$DIR/build"
TMP_DIR="/tmp/dragged-out-deploy"
REPO="git@github.com:paulkoan/draggedout.git"

cd "$DIR"

echo "=== Dragged Out Deploy ==="

# Setup venv if needed
if [ ! -d .venv ]; then
    echo "[setup] Creating venv..."
    python3 -m venv .venv
    .venv/bin/pip install pyyaml
fi

# Scrape + Build
echo "[build] Running generator..."
.venv/bin/python generator.py

# Prepare deploy directory
echo "[deploy] Pushing to gh-pages..."
rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"
cp -r "$BUILD_DIR"/* "$TMP_DIR"/

cd "$TMP_DIR"
git init -q
git checkout -b gh-pages -q
git config user.email "draggedout-bot@deploy"
git config user.name "Dragged Out Bot"
git add -A
git commit -q -m "Dragged Out update $(date -u '+%Y-%m-%d %H:%M UTC')"
GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=accept-new" git remote add origin "$REPO"
GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o StrictHostKeyChecking=accept-new" git push -f origin gh-pages 2>&1

echo ""
echo "✅ Dragged Out update complete — $(date -u '+%Y-%m-%d %H:%M UTC')"
