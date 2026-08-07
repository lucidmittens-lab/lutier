#!/usr/bin/env bash
#
# make_icon.sh -- turn one square source image into AppIcon.icns
#
# Run this ON YOUR MAC (not in any Linux/Claude sandbox) -- sips and
# iconutil are macOS-only tools and come pre-installed, nothing to
# download.
#
# Usage:
#   ./make_icon.sh AppIcon_source.png
#
# Source image should be square and as large as you can get it -- 1024x1024
# is ideal (AppIcon_source.png, copied from icon_design/icon_1024.png, is
# already exactly that).

set -euo pipefail

SRC="${1:-}"
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
  echo "Usage: $0 <source-image.png>"
  exit 1
fi

ICONSET="AppIcon.iconset"
rm -rf "$ICONSET" AppIcon.icns
mkdir "$ICONSET"

for sz in 16 32 128 256 512; do
  sips -z "$sz" "$sz" "$SRC" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
  sz2=$((sz * 2))
  sips -z "$sz2" "$sz2" "$SRC" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done

iconutil -c icns "$ICONSET" -o AppIcon.icns
rm -rf "$ICONSET"

echo "Done: AppIcon.icns created in $(pwd)."
echo "Next: rebuild the app so it picks it up --"
echo "  rm -rf build dist && python3 setup.py py2app"
