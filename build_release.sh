#!/bin/bash
# Build a distributable SwitchBored.app and its release zip.
#
# Usage:
#   ./build_release.sh
#
# Requires the python.org "macOS 64-bit universal2" build of Python 3.13.
# Homebrew or conda interpreters produce bundles that fail to launch on
# other machines or older macOS versions — the minos check below catches
# that before anything ships.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON=/usr/local/bin/python3.13

if [ ! -x "$PYTHON" ]; then
    echo "Error: $PYTHON not found." >&2
    echo "Install the python.org 'macOS 64-bit universal2' package:" >&2
    echo "  https://www.python.org/downloads/macos/" >&2
    exit 1
fi

# Refuse to bundle plugins that aren't meant for public distribution.
EXTRA_PLUGINS=$(find plugins -name "*.py" ! -name "__init__.py" ! -name "menu_template.py")
if [ -n "$EXTRA_PLUGINS" ]; then
    echo "Error: these plugin files would be bundled into the public app:" >&2
    echo "$EXTRA_PLUGINS" >&2
    echo "Move private plugins to ~/Library/Application Support/SwitchBored/plugins/" >&2
    exit 1
fi

VERSION=$("$PYTHON" -c "from version import VERSION; print(VERSION)")
echo "==> Building SwitchBored $VERSION"

echo "==> Cleaning build/, dist/, .venv/"
rm -rf build dist .venv

echo "==> Regenerating icon.icns"
./make_icns.sh

echo "==> Creating clean build environment"
"$PYTHON" -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt py2app

echo "==> Building the app bundle"
.venv/bin/python setup.py py2app > /dev/null

# The bundle must run on macOS 11+, not just this machine's OS release.
MINOS=$(vtool -show-build dist/SwitchBored.app/Contents/MacOS/python | awk '/minos/{print $2; exit}')
case "$MINOS" in
    10.*|11.*) ;;
    *)
        echo "Error: bundle requires macOS ${MINOS:-unknown} — built with the wrong Python?" >&2
        exit 1
        ;;
esac
echo "==> Minimum macOS: $MINOS"

echo "==> Zipping release asset"
ZIP="SwitchBored-$VERSION.zip"
(cd dist && ditto -c -k --keepParent SwitchBored.app "$ZIP")

echo ""
echo "Done: dist/SwitchBored.app  and  dist/$ZIP"
echo "Publish with:"
echo "  gh release create v$VERSION dist/$ZIP --title \"SwitchBored $VERSION\" --notes \"<paste from CHANGELOG.md>\""
