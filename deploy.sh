#!/usr/bin/env bash
# Dragged Out — weekly deploy script
# Uses deploy_key for auth — public key must be on GitHub as deploy key (write)

set -euo pipefail
cd "$(dirname "$0")"
PROJECT="Dragged Out"
REPO="paulkoan/draggedout"

echo "=== $PROJECT Deploy ==="

# ── Setup venv ──
if [ ! -d .venv ]; then
    echo "[setup] Creating venv..."
    python3 -m venv .venv
    .venv/bin/pip install pyyaml
fi

# ── Scrape + Build ──
echo "[build] Running generator..."
.venv/bin/python generator.py

# ── Git push via deploy key ──
echo "[git] Staging, committing, pushing..."
export GIT_SSH_COMMAND="ssh -F /dev/null -i deploy_key"

git -C . add -A
git -C . commit -m "deploy: site update $(date +%Y-%m-%d)" || echo "[git] No changes"
git -C . push git@github.com:paulkoan/draggedout.git main

echo "=== Done ==="