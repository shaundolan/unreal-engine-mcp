#!/usr/bin/env bash
# Mirror UnrealMCP/ (the source of truth) into the sample project's Plugins folder.
# Run after editing the plugin; tests/test_server_registration.py fails if they drift.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/UnrealMCP/"
dst="$root/FlopperamUnrealMCP/Plugins/UnrealMCP/"
mkdir -p "$dst"
rsync -a --delete --exclude Binaries/ --exclude Intermediate/ "$src" "$dst"
echo "synced $src -> $dst"
