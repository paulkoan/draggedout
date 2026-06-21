#!/usr/bin/env bash
# Deploy script for Dragged Out
# Run: ./deploy.sh
#
# Sets up a venv if missing, scrapes sources, builds site, pushes to GitHub.
# Requires GITHUB_TOKEN env var or configured git credential.

set -euo pipefail
cd "$(dirname "$0")"

echo "=== Dragged Out Deploy ==="
echo ""

# Setup venv if needed
if [ ! -d .venv ]; then
    echo "Creating venv..."
    python3 -m venv .venv
    .venv/bin/pip install pyyaml
fi

# Scrape + Build
echo "Building site..."
.venv/bin/python generator.py

# Git operations
echo ""
echo "Committing site..."
git add -A
git commit -m "deploy: site update $(date +%Y-%m-%d)" || echo "No changes to commit"

# Push to GitHub
echo "Pushing..."
git push origin main || echo "WARN: push failed — check auth"
echo ""
echo "=== Done ==="