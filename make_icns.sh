#!/bin/bash
# Generate icon.icns from a square PNG (ideally 1024x1024).
#
# Usage:
#   ./make_icns.sh [input.png] [output.icns]
#
# Defaults to icon.png -> icon.icns in the current directory.

set -euo pipefail

INPUT="${1:-icon.png}"
OUTPUT="${2:-icon.icns}"
ICONSET="$(mktemp -d)/icon.iconset"

if [ ! -f "$INPUT" ]; then
    echo "Error: '$INPUT' not found." >&2
    exit 1
fi

trap 'rm -rf "$(dirname "$ICONSET")"' EXIT
mkdir -p "$ICONSET"

for size in 16 32 64 128 256 512; do
    sips -z "$size" "$size" "$INPUT" --out "$ICONSET/icon_${size}x${size}.png" > /dev/null
    sips -z "$((size * 2))" "$((size * 2))" "$INPUT" --out "$ICONSET/icon_${size}x${size}@2x.png" > /dev/null
done

iconutil -c icns "$ICONSET" -o "$OUTPUT"

echo "Wrote $OUTPUT"
