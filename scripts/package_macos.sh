#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
application_path="$project_dir/dist/EquiSeek Legacy.app"
image_path="$project_dir/dist/EquiSeekLegacy-macOS.dmg"

if [[ ! -d "$application_path" ]]; then
  echo "Missing application bundle: $application_path" >&2
  echo "Run 'make desktop-legacy-build' first." >&2
  exit 1
fi

hdiutil create \
  -volname "EquiSeek Legacy" \
  -srcfolder "$application_path" \
  -ov \
  -format UDZO \
  "$image_path"

echo "$image_path"
