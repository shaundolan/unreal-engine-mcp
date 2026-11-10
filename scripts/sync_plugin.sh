#!/usr/bin/env bash
# Mirror UnrealMCP/ and UnrealMCPToolsets/ (the sources of truth) into the sample project's Plugins folder.
# Run after editing a plugin; tests/test_server_registration.py fails if they drift.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
for plugin in UnrealMCP UnrealMCPToolsets; do
  src="$root/$plugin/"
  dst="$root/FlopperamUnrealMCP/Plugins/$plugin/"
  mkdir -p "$dst"
  rsync -a --delete --exclude Binaries/ --exclude Intermediate/ "$src" "$dst"
  echo "synced $src -> $dst"
done
