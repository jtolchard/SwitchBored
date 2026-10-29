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

# Purge compiled bytecode so stale .pyc files (e.g. from a plugin that once
# lived here) can't be copied into the bundle.
find . -name "__pycache__" -type d -prune -exec rm -rf {} +

# Refuse to bundle plugins that aren't meant for public distribution.
EXTRA_PLUGINS=$(find plugins -name "*.py" ! -name "__init__.py" ! -name "menu_template.py")
if [ -n "$EXTRA_PLUGINS" ]; then
    echo "Error: these plugin files would be bundled into the public app:" >&2
    echo "$EXTRA_PLUGINS" >&2
    echo "Move private plugins to ~/Library/Application Support/SwitchBored/plugins/" >&2
    exit 1
fi

VERSION=$("$PYTHON" -c "from version import VERSION; print(VERSION)")

# The updater only reads versions like 1.5.1 (a release) or 1.5.2-dev.1 (a
# development build); anything else would never be offered to anyone.
if [[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)*-dev\.[0-9]+$ ]]; then
    DEV_BUILD=1
elif [[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
    DEV_BUILD=0
else
    echo "Error: version '$VERSION' must look like 1.5.1 or 1.5.2-dev.1." >&2
    exit 1
fi

# Releases are published from main and development builds from dev.
# Building from another branch is fine for testing, so this only warns.
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$DEV_BUILD" = 1 ] && [ "$BRANCH" != "dev" ]; then
    echo "Warning: building development build $VERSION on '$BRANCH'; publish dev builds from dev." >&2
elif [ "$DEV_BUILD" = 0 ] && [ "$BRANCH" != "main" ]; then
    echo "Warning: building release $VERSION on '$BRANCH'; publish releases from main." >&2
fi

if [ "$DEV_BUILD" = 1 ]; then
    echo "==> Building SwitchBored $VERSION (development build)"
else
    echo "==> Building SwitchBored $VERSION"
fi

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

# Some bundled libraries are arm64-only, so the app must never start under
# Rosetta; setup.py's LSRequiresNativeExecution makes sure of that.
NATIVE=$(/usr/libexec/PlistBuddy -c "Print :LSRequiresNativeExecution" \
    dist/SwitchBored.app/Contents/Info.plist 2>/dev/null || true)
if [ "$NATIVE" != "true" ]; then
    echo "Error: Info.plist is missing LSRequiresNativeExecution — check setup.py." >&2
    exit 1
fi
echo "==> Runs natively only (no Rosetta)"

echo "==> Zipping release asset"
ZIP="SwitchBored-$VERSION.zip"
(cd dist && ditto -c -k --keepParent SwitchBored.app "$ZIP")

echo ""
echo "Done: dist/SwitchBored.app  and  dist/$ZIP"
echo "Publish with:"
if [ "$DEV_BUILD" = 1 ]; then
    # --prerelease keeps it away from everyone who hasn't opted in, and
    # --target tags dev's latest commit (gh otherwise tags main).
    echo "  git push origin dev"
    echo "  gh release create v$VERSION dist/$ZIP --prerelease --target dev --title \"SwitchBored $VERSION (development build)\" --notes \"<what changed>\""
else
    echo "  gh release create v$VERSION dist/$ZIP --title \"SwitchBored $VERSION\" --notes \"<paste from CHANGELOG.md>\""
fi
